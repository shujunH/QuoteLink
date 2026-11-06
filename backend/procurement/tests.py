import io
import json
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from .models import Draft, Inquiry, Quote
from .pricing import evaluate_quote


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=io.StringIO())

    def setUp(self):
        self.env = patch.dict("os.environ", {"QUOTELINK_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client.get("/api/workspace/")

    def ask(self, message, item=None):
        data = {"message": message}
        if item:
            data.update(inquiry_id=item["id"], revision=item["revision"])
        return self.client.post("/api/chat/", data, content_type="application/json")

    def sample(self):
        response = self.ask("下周三前要到货20台显示器，总预算2.4万元，含运费。")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def confirm(self, item):
        revision = item["comparison_revision"]
        code = item["turns"][revision - 1]["recommended"] if revision else None
        return self.client.post(f"/api/inquiries/{item['id']}/confirm/",
                                {"revision": item["revision"], "code": code},
                                content_type="application/json")

    def test_recommend_then_lower_budget(self):
        inquiry = self.sample()
        first = inquiry["turns"][0]
        self.assertEqual(first["recommended"], "QT-1002")
        self.assertEqual([r["total"] for r in first["rows"]], ["22000.00", "23500.00", "24200.00"])
        self.assertIn("缺 8", first["rows"][0]["reasons"][0])
        self.assertIn("200.00", first["rows"][2]["reasons"][0])
        changed = self.ask("预算改成2.3万，其他条件不变。", inquiry).json()
        self.assertIsNone(changed["turns"][-1]["recommended"])
        self.assertEqual(changed["needs"], {**inquiry["needs"], "budget": "23000.00"})
        self.assertEqual(self.confirm(inquiry).status_code, 409)
        self.assertEqual(self.confirm(changed).status_code, 400)

    def test_missing_deadline_and_boundary(self):
        partial = self.ask("采购20台显示器，预算23500元").json()
        self.assertIn("最晚到货日", partial["turns"][0]["message"])
        self.assertFalse(partial["turns"][0]["rows"])
        complete = self.ask("后天到货", partial).json()
        self.assertEqual(complete["turns"][-1]["recommended"], "QT-1002")
        late = self.ask("改成明天到货", complete).json()
        self.assertIn("超过交期", late["turns"][-1]["rows"][1]["reasons"][0])

    def test_confirm_idempotent_and_isolated(self):
        inquiry = self.sample()
        first, replay = self.confirm(inquiry), self.confirm(inquiry)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(Draft.objects.count(), 1)
        stranger = Client()
        self.assertFalse(stranger.get("/api/workspace/").json()["drafts"])
        self.assertEqual(stranger.get(f"/api/inquiries/{inquiry['id']}/").status_code, 404)
        self.assertEqual(stranger.post(f"/api/inquiries/{inquiry['id']}/confirm/", {},
                                       content_type="application/json").status_code, 404)

    def test_changed_price_requires_new_comparison(self):
        inquiry = self.sample()
        Quote.objects.filter(code="QT-1002").update(unit_price="1161.00")
        self.assertEqual(self.confirm(inquiry).status_code, 409)
        self.assertFalse(Draft.objects.exists())
        refreshed = self.ask("重新比价", inquiry).json()
        self.assertEqual(self.confirm(refreshed).json()["quote"]["total"], "23520.00")

    def test_new_cheaper_offer_invalidates_recommendation(self):
        inquiry = self.sample()
        Quote.objects.filter(code="QT-1001").update(stock=20)
        self.assertEqual(self.confirm(inquiry).status_code, 409)

    def test_deactivated_quote_and_unknown_product(self):
        inquiry = self.sample()
        Quote.objects.filter(code="QT-1002").update(active=False)
        self.assertEqual(self.confirm(inquiry).status_code, 409)
        other = self.ask("采购20台打印机，预算24000元，下周三到货").json()
        self.assertIn("暂无", other["turns"][0]["message"])

    def test_invalid_input_and_stale_followup(self):
        self.assertEqual(self.ask("预算-100元").status_code, 400)
        self.assertEqual(self.ask("采购0台显示器").status_code, 400)
        self.assertEqual(self.ask("2026-02-30到货").status_code, 400)
        inquiry = self.sample()
        self.ask("预算改成3万元", inquiry)
        self.assertEqual(self.ask("预算改成4万元", inquiry).status_code, 409)

    def test_freight_is_added_once(self):
        quote = Quote.objects.get(code="QT-1002")
        today = date(2026, 11, 6)
        needs = {"quantity": 20, "budget": "23200.00", "deadline": (today + timedelta(days=2)).isoformat()}
        row = evaluate_quote(quote, needs, today)
        self.assertEqual(row["reasons"], ["超预算 ¥300.00"])

    def test_model_protocol_with_simulated_provider(self):
        # 真实 LangChain 客户端与本地模拟供应商交互，检查工具结果确实回传到下一轮。
        with simulated_provider() as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            inquiry = self.sample()
        self.assertEqual(len(calls), 2)
        self.assertEqual([t["function"]["name"] for t in calls[0]["tools"]],
                         ["find_quotes", "evaluate_quote", "list_products", "read_comparison"])
        self.assertEqual(calls[1]["messages"][-1]["role"], "tool")
        self.assertIn("QT-1002", calls[1]["messages"][-1]["content"])
        self.assertEqual(inquiry["turns"][0]["recommended"], "QT-1002")
        self.assertEqual(len(inquiry["turns"][0]["trace"]), 4)

    def test_faq_catalog_and_followup_confirmation(self):
        faq = self.ask("采购品类有哪些？").json()
        self.assertIn("直接采购", faq["turns"][-1]["message"])
        self.assertIsNone(faq["comparison_revision"])
        self.assertEqual(self.confirm(faq).status_code, 400)
        catalog = self.ask("你们平台有哪些商品？", faq).json()
        self.assertEqual(catalog["turns"][-1]["trace"][0]["result"], ["显示器"])
        quote = self.ask("20台显示器，预算24000元，后天到货", catalog).json()
        # 历史询价没有 kind 字段，追问后应继续定位到原始比价轮次。
        quote["turns"][-1].pop("kind")
        Inquiry.objects.filter(pk=quote["id"]).update(turns=quote["turns"])
        explanation = self.ask("刚才青禾数码的运费是多少？", quote).json()
        self.assertIn("整单运费 300.00 元", explanation["turns"][-1]["message"])
        saved = self.confirm(explanation)
        self.assertEqual(saved.status_code, 201)
        more = self.ask("预算包含运费吗？", explanation).json()
        self.assertEqual(more["needs"], quote["needs"])
        self.assertEqual(more["comparison_revision"], quote["revision"])
        self.assertEqual(more["draft"], saved.json())
        self.assertEqual(self.confirm(more).json(), saved.json())
        changed = self.ask("预算改成2万元", more).json()
        self.assertIsNone(changed["draft"])
        self.assertEqual(self.confirm(changed).status_code, 400)

    def test_model_faq_catalog_and_snapshot(self):
        # 同一个模拟模型先直接回答，再读取商品目录及已保存的报价快照。
        quote = self.sample()
        Quote.objects.filter(code="QT-1002").update(freight="999.00")
        responses = ["运费成本是运输商品产生的费用。", "按用途可分为直接采购和间接采购。",
                     [("list_products", {})], [("read_comparison", {})],
                     "上次比价中，青禾数码的整单运费为 300 元。"]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            faq = self.ask("什么是运费成本？", quote).json()
            self.assertEqual(faq["turns"][-1]["message"], responses[0])
            categories = self.ask("采购品类有哪些？", faq).json()
            self.assertNotIn("tools", calls[-1])
            self.assertEqual(categories["turns"][-1]["message"], responses[1])
            catalog = self.ask("平台有哪些商品？", categories).json()
            self.assertIn("显示器", catalog["turns"][-1]["message"])
            explained = self.ask("刚才推荐的那家收多少运费？", catalog).json()
        self.assertEqual(explained["turns"][-1]["message"], responses[-1])
        snapshot = json.loads(calls[-1]["messages"][-1]["content"])
        self.assertEqual(next(r["freight"] for r in snapshot["rows"] if r["code"] == "QT-1002"), "300.00")
        self.assertEqual(explained["needs"], quote["needs"])
        self.assertEqual(explained["comparison_revision"], quote["revision"])
        self.assertEqual(self.confirm(explained).status_code, 409)


@contextmanager
def simulated_provider(responses=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if responses is not None:
                actions = responses[len(calls) - 1]
            elif len(calls) == 1:
                args = {"product": "显示器", "quantity": 20, "budget": "24000",
                        "deadline": (timezone.localdate() + timedelta(days=5)).isoformat()}
                actions = [("find_quotes", args)]
            else:
                actions = [("evaluate_quote", {"code": code}) for code in ("QT-1001", "QT-1002", "QT-1003")]
            if isinstance(actions, str):
                message, reason = {"role": "assistant", "content": actions}, "stop"
            else:
                tool_calls = [{"id": f"call-{len(calls)}-{i}", "type": "function",
                               "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(actions)]
                message, reason = {"role": "assistant", "content": None, "tool_calls": tool_calls}, "tool_calls"
            payload = json.dumps({"id": "local", "object": "chat.completion", "created": 1, "model": "fixture",
                                  "choices": [{"index": 0, "finish_reason": reason, "message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
        finally:
            server.shutdown()
            thread.join()
