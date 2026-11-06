"""采购问答共用的数据来源，以及本地演示支持的常见问题。"""
import re

from .models import Quote


PLATFORM_RULES = (
    "平台比较单品、单供应商的整单报价，需要品名、数量、含运费总预算和最晚到货日。"
    "含运费总价等于单价乘以采购数量，再加一次整单运费。"
    "库存、到货日和预算都达标时，优先推荐含运总价最低的报价，同价按报价编号排序。"
    "用户可在推荐结果下确认并保存采购草稿，确认时系统会复核报价。"
)


def comparison_revision(turns):
    """问答不占用比价版本；旧记录均为询价轮次，继续按原版本关联草稿。"""
    return next((i for i in range(len(turns), 0, -1) if turns[i - 1].get("kind") != "answer"), None)


def active_products():
    """只列出至少有一份有效报价的实际品名。"""
    return list(Quote.objects.filter(active=True).order_by("product").values_list("product", flat=True).distinct())


def catalog_answer(products):
    return "目前有有效报价的商品有：" + "、".join(products) + "。" if products else "平台目前没有有效的商品报价。"


def answer_result(message):
    return {"kind": "answer", "message": message, "rows": [], "recommended": None}


def is_category_question(text):
    """平台目录需要明确的平台指代；单独问采购分类属于通用知识。"""
    return bool(re.fullmatch(
        r"(?:请问|请介绍(?:一下)?|介绍一下)?(?:常见的?|一般的?)?采购(?:品类|类别|分类|种类)"
        r"(?:通常|一般|主要|常见的?)?(?:有哪些|有哪几种|有什么|有几种|怎么分|是什么)?[呢啊吗？?。！!\s]*",
        text.strip(),
    ))


def local_answer(text, comparison):
    """离线模式仅回答已覆盖的问法；询价和条件变更继续交给原来的解析流程。"""
    if not is_category_question(text) and not re.search(r"什么|哪[些家个]|几[种类]|怎么|如何|为什么|多少|为啥|为何|吗|[？?]|介绍|解释|列出", text):
        return None
    if re.search(r"平台|你们|报价库|可以询价|能询价", text) and re.search(r"品类|商品|品名|产品|买什么|采购什么", text):
        products = active_products()
        return catalog_answer(products), [{"tool": "list_products", "args": {}, "result": products}]
    rows = comparison.get("rows", []) if comparison else []
    named = [r for r in rows if r["supplier"] in text or r["code"].lower() in text.lower()]
    if named or re.search(r"这家|那家|刚才|上一轮|上次|推荐的|为什么推荐|为何推荐|QT-\d+", text, re.I):
        if not rows:
            return "当前还没有可解释的比价明细。可以先提交品名、数量、总预算和最晚到货日。", []
        if re.search(r"QT-\d+", text, re.I) and not named:
            return "本次比价中没有这个报价编号。", []
        if "运费" in text and "推荐" in text and not named:
            named = [r for r in rows if r["code"] == comparison["recommended"]]
        details = [f"{r['supplier']}（{r['code']}）：整单运费 {r['freight']} 元，含运总价 {r['total']} 元，"
                   + ("满足全部条件。" if r["feasible"] else "；".join(r["reasons"]) + "。") for r in named or rows]
        return "\n".join([comparison["message"], *details]), []
    if re.search(r"品类|采购分类|采购种类|采购类别|采购.*分[为几]", text):
        return ("按用途，常见的有直接采购（如生产原料、零部件）和间接采购（如办公用品、设备维护服务）。"
                "按采购对象，也可以分为货物、工程和服务。"), []
    if re.search(r"运费成本|运输成本|什么是运费|运费是什么|运费怎么算|含运费吗|[含包算计].{0,3}运费[吗么]", text):
        return ("运费成本是商品从供应商运到收货地点产生的运输费用。平台按整单计一次运费。"
                "例如买 10 件商品，每件 90 元，整单运费 50 元，含运费总成本就是 950 元。"), []
    if re.search(r"准备|询价流程|如何询价|怎么询价|保存草稿|生成草稿|推荐规则", text):
        return PLATFORM_RULES, []
    return None
