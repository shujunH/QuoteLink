import json
import os
import re
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Quote
from .pricing import LABELS, InputError, evaluate_quote, find_quotes, merge_needs, quote_data, result_for
from .questions import (PLATFORM_RULES, active_products, answer_result, catalog_answer,
                        comparison_revision, is_category_question, local_answer)


class AgentUnavailable(Exception):
    pass


def mode():
    return "model" if os.getenv("QUOTELINK_API_KEY") else "local"


def local_patch(text, previous, today):
    """离线演示识别常见中文句式，追问只覆盖本轮提及的字段。"""
    compact = re.sub(r"\s+", "", text.replace("，", ","))
    patch = {}
    products = list(Quote.objects.filter(active=True).values_list("product", flat=True).distinct())
    matches = {p for p in products if p in compact}
    if len(matches) > 1:
        raise InputError("每次询价支持一种商品，请分开提交。")
    if matches:
        patch["product"] = matches.pop()
    elif m := re.search(r"(?:采购品名|品名|商品)[：:]([^,。；;]+)", compact):
        patch["product"] = m[1]
    elif m := re.search(r"\d+(?:台|件|套|个)([\u4e00-\u9fff]{1,30}?)(?=[,。；;]|总预算|预算|$)", compact):
        patch["product"] = m[1]
    qty = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:台|件|套|个)", compact)
    if not qty:
        qty = re.search(r"数量(?:调整为|改成|改为|为|是|[：:])?(-?\d+(?:\.\d+)?)", compact)
    if qty:
        patch["quantity"] = qty[1]
    money = re.search(r"(?:预算|上限)(?:调整为|改成|调到|改为|降至|提高到|不超过|控制在|为|是|到|[：:])?[¥￥]?(-?\d+(?:,\d{3})*(?:\.\d+)?)(万|千)?", compact)
    if not money:
        money = re.search(r"(-?\d+(?:\.\d+)?)(万|千)?元", compact)
    if money:
        patch["budget"] = Decimal(money[1].replace(",", "")) * {"万": 10000, "千": 1000}.get(money[2], 1)
    if m := re.search(r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})", compact):
        try:
            patch["deadline"] = date(*map(int, m.groups())).isoformat()
        except ValueError:
            raise InputError("到货日期无效，请使用 YYYY-MM-DD。")
    elif m := re.search(r"(下周|本周|这周|周)([一二三四五六日天])", compact):
        weekday = "一二三四五六日天".index(m[2])
        delta = min(weekday, 6) - today.weekday()
        if m[1] == "下周" or (m[1] == "周" and delta < 0):
            delta += 7
        patch["deadline"] = (today + timedelta(days=delta)).isoformat()
    elif m := re.search("今天|明天|后天", compact):
        patch["deadline"] = (today + timedelta(days=["今天", "明天", "后天"].index(m[0]))).isoformat()
    if not patch and not (previous and any(word in compact for word in ["重新", "再比", "再查"])):
        raise InputError("本地演示可识别品名、数量、预算和日期，例如「20台显示器，预算2.4万元，下周三前到货」。")
    return patch


def run_local(text, previous, today, comparison):
    reply = local_answer(text, comparison)
    if reply:
        message, trace = reply
        return previous, answer_result(message), trace
    needs = merge_needs(previous, local_patch(text, previous, today))
    trace, rows = [], []
    if LABELS.keys() <= needs.keys():
        quotes = find_quotes(needs["product"])
        trace.append({"tool": "find_quotes", "args": {"product": needs["product"]}, "result": [q.code for q in quotes]})
        for quote in quotes:
            row = evaluate_quote(quote, needs, today)
            rows.append(row)
            trace.append({"tool": "evaluate_quote", "args": {"code": quote.code}, "result": row})
    return needs, result_for(needs, rows), trace


def run_model(text, previous, today, turns, comparison):
    """模型直接回答知识问题，查询平台事实时使用只读工具，实际比价由后端核算。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel, Field

    class FindArgs(BaseModel):
        product: str | None = Field(None, description="本轮明确提及的单一品名")
        quantity: int | None = Field(None, description="本轮明确提及的数量")
        budget: str | None = Field(None, description="本轮明确提及的含运费总预算，换算为元")
        deadline: date | None = Field(None, description="本轮明确提及的最晚到货日期")

    class EvaluateArgs(BaseModel):
        code: str = Field(description="find_quotes 返回的一个报价编号")

    class NoArgs(BaseModel):
        pass

    if not os.getenv("QUOTELINK_MODEL"):
        raise AgentUnavailable("请在后端 .env 中设置 QUOTELINK_MODEL。")
    model = ChatOpenAI(model=os.environ["QUOTELINK_MODEL"], api_key=os.environ["QUOTELINK_API_KEY"],
                       base_url=os.getenv("QUOTELINK_BASE_URL") or None, timeout=25, max_retries=0)
    schemas = [{"type": "function", "function": {"name": name, "description": description,
                "parameters": args.model_json_schema()}} for name, description, args in [
                    ("find_quotes", "合并明确给出的采购条件并查询报价", FindArgs),
                    ("evaluate_quote", "按后端保存的需求核算一个候选报价", EvaluateArgs),
                    ("list_products", "列出平台当前有有效报价的品名", NoArgs),
                    ("read_comparison", "读取当前会话最近一轮询价的条件、推荐和明细，用于解释报价", NoArgs)]]
    messages = [SystemMessage(content=f"你是采价通采购助手。今天 {today}。已有条件 {json.dumps(previous, ensure_ascii=False)}。"
                + PLATFORM_RULES +
                "用简洁的中文纯文本回答，可分段，避免 Markdown 标记。通用采购概念可举明确标注的假设例子。"
                "“采购品类有哪些”请按通用采购分类回答。仅当明确询问本平台可询价的商品时调用 list_products。"
                "平台操作问题依据上述规则。追问具体报价、运费、推荐原因时调用 read_comparison，"
                "只依据该快照解释，缺少依据就说明。问题里的举例和提问不代表要变更采购条件。"
                "仅在用户发起采购、补充或调整条件、请求重新比价时调用 find_quotes，只提交明确变更。"
                "随后为每个候选调用 evaluate_quote，可并行。多品采购或指代不明时先提问。")]
    # 最近的对话用于理解省略和指代，具体报价以本次工具读取的业务数据为准。
    for turn in turns[-4:]:
        messages.extend([HumanMessage(content=turn["user"]), AIMessage(content=turn["message"])])
    messages.append(HumanMessage(content=text))
    needs, candidates, evaluated, trace = dict(previous), {}, {}, []
    searched = False
    # 明确的通用品类问题无需报价库，避免将采购分类误解为平台商品目录。
    responder = model if is_category_question(text) else model.bind_tools(schemas)
    for _ in range(8):
        answer = responder.invoke(messages)
        messages.append(answer)
        if not answer.tool_calls:
            if not searched and isinstance(answer.content, str) and answer.content.strip():
                return needs, answer_result(answer.content.strip()), trace
            break
        for call in answer.tool_calls:
            name, args = call["name"], call["args"]
            # 只读问答单独结束本轮，不更新条件，也不产生可确认的新推荐。
            if name in ("list_products", "read_comparison"):
                if searched or len(answer.tool_calls) != 1 or args:
                    raise AgentUnavailable("模型返回了冲突的查询，请重新提问。")
                if name == "list_products":
                    products = active_products()
                    trace.append({"tool": name, "args": {}, "result": products})
                    return needs, answer_result(catalog_answer(products)), trace
                snapshot = {k: comparison[k] for k in ("needs", "rows", "recommended", "message")} if comparison else {}
                trace.append({"tool": name, "args": {}, "result": snapshot})
                if not snapshot.get("rows"):
                    return needs, answer_result("当前还没有比价明细。请先完成一次询价。"), trace
                messages.append(ToolMessage(content=json.dumps(snapshot, ensure_ascii=False), tool_call_id=call["id"]))
                explanation = model.invoke(messages).content
                if not isinstance(explanation, str) or not explanation.strip():
                    raise AgentUnavailable("模型未能解释本次报价，请重试。")
                return needs, answer_result(explanation.strip()), trace
            if name == "find_quotes" and not searched:
                patch = FindArgs.model_validate(args).model_dump(mode="json", exclude_none=True)
                needs = merge_needs(previous, patch)
                candidates = {q.code: q for q in find_quotes(needs["product"])} if "product" in needs else {}
                payload = [quote_data(q, today) for q in candidates.values()]
                searched = True
            elif name == "evaluate_quote" and searched and LABELS.keys() <= needs.keys() and args.get("code") in candidates:
                code = args["code"]
                payload = evaluated[code] = evaluate_quote(candidates[code], needs, today)
            else:
                raise AgentUnavailable("模型返回了无效的工具调用，请重新询价。")
            trace.append({"tool": name, "args": args, "result": payload})
            messages.append(ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call["id"]))
        if searched and (not LABELS.keys() <= needs.keys() or evaluated.keys() == candidates.keys()):
            rows = [evaluated[c] for c in candidates if c in evaluated]
            return needs, result_for(needs, rows), trace
    raise AgentUnavailable("模型未完成全部报价核算，请重试。")


def run_turn(text, previous, turns=()):
    today = timezone.localdate()
    revision = comparison_revision(turns)
    comparison = turns[revision - 1] if revision else None
    if mode() == "local":
        return run_local(text, previous, today, comparison)
    try:
        return run_model(text, previous, today, turns, comparison)
    except (InputError, AgentUnavailable):
        raise
    except Exception as exc:
        raise AgentUnavailable("模型服务调用失败，请检查后端模型配置及网络后重试。") from exc
