import json
import uuid
from functools import wraps

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .agent import AgentUnavailable, mode, run_turn
from .models import Draft, Inquiry, Quote
from .pricing import InputError, evaluate_quote, find_quotes, quote_data, result_for
from .questions import comparison_revision


class Conflict(Exception):
    pass


def api_errors(view):
    """业务错误统一返回 JSON，跨会话访问返回相同的未找到响应。"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except InputError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Inquiry.DoesNotExist:
            return JsonResponse({"error": "找不到这条询价。"}, status=404)
        except Conflict as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        except AgentUnavailable as exc:
            return JsonResponse({"error": str(exc)}, status=503)
    return wrapped


def body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise InputError("请求必须是 JSON 对象。")
    if not isinstance(data, dict):
        raise InputError("请求必须是 JSON 对象。")
    return data


def owner(request):
    if "workspace" not in request.session:
        request.session["workspace"] = uuid.uuid4().hex
    return request.session["workspace"]


def draft_data(draft):
    return {"id": draft.pk, "number": f"PR-{draft.pk:05d}", "needs": draft.needs,
            "quote": draft.quote, "created": draft.created.isoformat(), "inquiry_id": str(draft.inquiry_id)}


def detail(inquiry):
    revision = comparison_revision(inquiry.turns)
    draft = Draft.objects.filter(inquiry=inquiry, revision=revision).first() if revision else None
    return {"id": str(inquiry.pk), "revision": inquiry.revision, "comparison_revision": revision, "needs": inquiry.needs,
            "turns": inquiry.turns, "draft": draft_data(draft) if draft else None}


@require_GET
@ensure_csrf_cookie
def workspace(request):
    """初始化浏览器工作区，列出公开报价与当前用户自己的历史及草稿。"""
    account = owner(request)
    today = timezone.localdate()
    inquiries = Inquiry.objects.filter(owner=account).order_by("-updated")
    return JsonResponse({
        "mode": mode(), "today": today.isoformat(),
        "quotes": [quote_data(q, today) for q in Quote.objects.filter(active=True)],
        "history": [{"id": str(i.pk), "title": i.turns[0]["user"], "updated": i.updated.isoformat()} for i in inquiries],
        "drafts": [draft_data(d) for d in Draft.objects.filter(inquiry__owner=account).order_by("-created")],
    })


@require_GET
@api_errors
def inquiry_detail(request, pk):
    return JsonResponse(detail(Inquiry.objects.get(pk=pk, owner=owner(request))))


@require_POST
@api_errors
def chat(request):
    data = body(request)
    text = data.get("message")
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 1200:
        raise InputError("请输入 1 至 1200 字的采购问题或询价需求。")
    account = owner(request)
    inquiry = None
    if data.get("inquiry_id") is not None:
        try:
            pk = uuid.UUID(str(data["inquiry_id"]))
        except ValueError:
            raise InputError("询价编号无效。")
        inquiry = Inquiry.objects.get(pk=pk, owner=account)
        if data.get("revision") != inquiry.revision:
            raise Conflict("询价已更新，请重新打开记录再提交。")
    needs, result, trace = run_turn(text.strip(), inquiry.needs if inquiry else {}, inquiry.turns if inquiry else [])
    turn = {"user": text.strip(), "needs": needs, "kind": "inquiry", **result, "trace": trace, "mode": mode()}
    # 等工具返回后再开启事务，旧页面无法覆盖已提交的后续轮次。
    with transaction.atomic():
        if inquiry:
            latest = Inquiry.objects.get(pk=inquiry.pk)
            if latest.revision != inquiry.revision:
                raise Conflict("另一轮询价已完成，请重新打开记录。")
        else:
            inquiry = Inquiry(owner=account)
        inquiry.needs = needs
        inquiry.revision += 1
        inquiry.turns = [*inquiry.turns, turn]
        inquiry.save()
    return JsonResponse(detail(inquiry))


@require_POST
@api_errors
def confirm(request, pk):
    data = body(request)
    # 同一事务内重新读取全部候选报价；确认价变动或推荐变化均要求重新比价。
    with transaction.atomic():
        inquiry = Inquiry.objects.get(pk=pk, owner=owner(request))
        if data.get("revision") != inquiry.revision:
            raise Conflict("询价条件已更新，请确认最新一轮结果。")
        # 会话版本检测过期请求，草稿按最近一次询价的版本去重，穿插问答后仍可确认。
        revision = comparison_revision(inquiry.turns)
        if revision is None:
            raise InputError("请先完成一次比价，再确认报价。")
        last = inquiry.turns[revision - 1]
        code = last["recommended"]
        if not code or data.get("code") != code:
            raise InputError("只能确认当前轮次推荐的有效报价。")
        saved = Draft.objects.filter(inquiry=inquiry, revision=revision).first()
        if saved:
            return JsonResponse(draft_data(saved))
        today = timezone.localdate()
        rows = [evaluate_quote(q, inquiry.needs, today) for q in find_quotes(inquiry.needs["product"])]
        current = next((row for row in rows if row["code"] == code), None)
        original = next(row for row in last["rows"] if row["code"] == code)
        if current != original or result_for(inquiry.needs, rows)["recommended"] != code:
            raise Conflict("供应条件或报价已变化，请发送「重新比价」后再确认。")
        draft = Draft.objects.create(inquiry=inquiry, revision=revision, needs=inquiry.needs, quote=current)
    return JsonResponse(draft_data(draft), status=201)
