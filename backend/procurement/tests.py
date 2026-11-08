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
from .pricing import InputError, evaluate_quote, query_quotes


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
                         ["find_quotes", "evaluate_quote", "list_products", "read_comparison", "query_quotes"])
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
                     [("list_products", {})], "目前有有效报价的商品有：显示器。", [("read_comparison", {})],
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


    def test_model_queries_another_product_and_refreshes_prices(self):
        # 跨商品浏览当前报价不会覆盖采购条件；再次查询读取改价后的数据库。
        inquiry = self.sample()
        paper = Quote.objects.create(code="PAPER-01", supplier="演示纸业", product="A4打印纸",
                                     unit_price="25.50", freight="30", stock=60, lead_days=2)
        Quote.objects.create(code="PAPER-OFF", supplier="停用纸业", product="A4打印纸",
                             unit_price="1", freight="0", stock=999, lead_days=1, active=False)
        responses = [[("query_quotes", {"product": "a4 打印纸"})], "演示纸业的 A4打印纸单价25.50元，整单运费30元。",
                     [("query_quotes", {"product": "A4打印纸"})], "当前单价已变为26.00元。"]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            first = self.ask("告诉我A4打印纸都有哪些厂商和报价", inquiry)
            self.assertEqual(first.status_code, 200, first.content)
            first = first.json()
            paper.unit_price = "26.00"
            paper.save(update_fields=["unit_price"])
            refreshed = self.ask("再查一下A4打印纸现在的价格", first).json()
        for item in (first, refreshed):
            self.assertEqual(item["needs"], inquiry["needs"])
            self.assertEqual(item["comparison_revision"], inquiry["comparison_revision"])
            self.assertEqual(item["turns"][-1]["kind"], "answer")
            self.assertFalse(item["turns"][-1]["rows"])
        initial_payload = json.loads(calls[1]["messages"][-1]["content"])
        latest_payload = json.loads(calls[3]["messages"][-1]["content"])
        self.assertEqual(initial_payload["total"], 1)
        self.assertEqual(initial_payload["query"]["product"], "A4打印纸")
        self.assertEqual(initial_payload["quotes"][0]["unit_price"], "25.50")
        self.assertEqual(latest_payload["quotes"][0]["unit_price"], "26.00")
        self.assertEqual(refreshed["turns"][-1]["trace"][0]["tool"], "query_quotes")
        self.assertEqual({t["function"]["name"] for t in calls[1]["tools"]},
                         {"list_products", "read_comparison", "query_quotes"})
        self.assertEqual(self.confirm(refreshed).status_code, 201)

    def test_model_supplier_query_without_procurement_conditions(self):
        responses = [[("query_quotes", {"supplier": "青禾", "limit": 1})], "青禾数码当前有显示器报价，单价1160元。"]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("青禾数码有哪些商品和报价？")
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()
        self.assertEqual(item["needs"], {})
        self.assertIsNone(item["comparison_revision"])
        self.assertEqual(self.confirm(item).status_code, 400)
        payload = json.loads(calls[1]["messages"][-1]["content"])
        self.assertEqual(payload["quotes"][0]["code"], "QT-1002")

    def test_model_unknown_product_does_not_use_old_comparison(self):
        inquiry = self.sample()
        responses = [[("query_quotes", {"product": "不存在的商品"})], "当前没有查到这个商品的有效报价。"]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("不存在的商品有哪些报价？", inquiry)
        self.assertEqual(response.status_code, 200, response.content)
        payload = json.loads(calls[1]["messages"][-1]["content"])
        self.assertEqual(payload["status"], "not_found")
        self.assertFalse(payload["quotes"])
        self.assertEqual(response.json()["needs"], inquiry["needs"])

    def test_model_rejects_unexpected_query_fields_without_saving_turn(self):
        responses = [[("query_quotes", {"supplier": "青禾", "sql": "select * from auth_user"})]]
        with simulated_provider(responses) as (base, _), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("青禾数码有哪些报价？")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Inquiry.objects.exists())

    def test_model_reads_catalog_then_all_77_quotes_across_pages(self):
        # 真实客户端连续读取目录和两页报价，确保 19 种商品的 77 条结果无遗漏。
        inquiry = self.sample()
        Quote.objects.bulk_create([
            Quote(code=f"BULK-{i:03d}", supplier=f"演示供货商{i % 4}",
                  product=f"商品{i // 4:02d}" if i < 72 else "显示器",
                  unit_price=str(10 + i) if i < 72 else "5000", freight="10", stock=100, lead_days=2)
            for i in range(74)
        ])
        responses = [[("list_products", {})], [("query_quotes", {"all_products": True, "limit": 50})],
                     [("query_quotes", {"all_products": True, "limit": 50, "offset": 50})],
                     "已读取19种商品的全部77条有效报价，下面按商品列出供应商和单价。"]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("我要所有产品的名称和对应报价", inquiry)
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()
        self.assertEqual(item["needs"], inquiry["needs"])
        self.assertEqual(item["comparison_revision"], inquiry["comparison_revision"])
        self.assertEqual(item["turns"][-1]["kind"], "answer")
        self.assertFalse(item["turns"][-1]["rows"])
        self.assertEqual([t["tool"] for t in item["turns"][-1]["trace"]],
                         ["list_products", "query_quotes", "query_quotes"])
        catalog = json.loads(calls[1]["messages"][-1]["content"])
        first = json.loads(calls[2]["messages"][-1]["content"])
        last = json.loads(calls[3]["messages"][-1]["content"])
        self.assertEqual(len(catalog), 19)
        self.assertEqual(first["query"], {"all_products": True})
        self.assertEqual((first["total"], len(first["quotes"]), first["next_offset"]), (77, 50, 50))
        self.assertEqual((last["total"], len(last["quotes"]), last["next_offset"]), (77, 27, None))
        self.assertEqual({q["code"] for q in first["quotes"] + last["quotes"]},
                         set(Quote.objects.values_list("code", flat=True)))
        self.assertEqual(self.confirm(item).status_code, 201)

    def test_model_batches_multiple_read_only_queries(self):
        inquiry = self.sample()
        Quote.objects.create(code="PAPER-01", supplier="演示纸业", product="A4打印纸",
                             unit_price="25.50", freight="30", stock=60, lead_days=2)
        responses = [[("query_quotes", {"product": "显示器"}),
                      ("query_quotes", {"product": "A4打印纸"}), ("read_comparison", {})],
                     "显示器与A4打印纸当前报价已分别列出，历史显示器推荐仍为青禾数码。"]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("同时查看显示器和A4打印纸的报价，并解释刚才的推荐", inquiry)
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()
        tool_messages = calls[1]["messages"][-3:]
        self.assertTrue(all(m["role"] == "tool" for m in tool_messages))
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["call-1-0", "call-1-1", "call-1-2"])
        self.assertEqual(json.loads(tool_messages[0]["content"])["total"], 3)
        self.assertEqual(json.loads(tool_messages[1]["content"])["total"], 1)
        self.assertEqual(json.loads(tool_messages[2]["content"])["recommended"], "QT-1002")
        self.assertEqual(item["needs"], inquiry["needs"])
        self.assertEqual(self.confirm(item).status_code, 201)

    def test_model_rejects_mixed_read_and_procurement_batches_before_execution(self):
        actions = [("query_quotes", {"all_products": True}), ("find_quotes", {"product": "显示器"})]
        for batch in (actions, list(reversed(actions))):
            with self.subTest(batch=batch), simulated_provider([batch]) as (base, _), patch.dict("os.environ", {
                "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
            }), patch("procurement.agent.query_quotes") as query:
                response = self.ask("查看所有报价并发起显示器采购")
                query.assert_not_called()
            self.assertEqual(response.status_code, 503)
            self.assertFalse(Inquiry.objects.exists())

    def test_model_cannot_switch_from_read_only_to_procurement(self):
        responses = [[("query_quotes", {"all_products": True})], [("find_quotes", {"product": "显示器"})]]
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("列出所有产品和报价")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("find_quotes", {t["function"]["name"] for t in calls[1]["tools"]})
        self.assertFalse(Inquiry.objects.exists())

    def test_read_round_limit_summarizes_only_loaded_pages(self):
        # 模型反复查询时有明确上限，最后禁用工具并说明尚未读取的分页。
        responses = [[("query_quotes", {"all_products": True, "limit": 1})] for _ in range(8)]
        responses.append("当前只读取了部分报价，尚未列全，可以继续查询后续分页。")
        with simulated_provider(responses) as (base, calls), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }):
            response = self.ask("列出所有产品和报价")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(calls), 9)
        self.assertNotIn("tools", calls[-1])
        self.assertIn("未读取全部报价", calls[-1]["messages"][-1]["content"])
        self.assertEqual(len(response.json()["turns"][-1]["trace"]), 8)
        self.assertIn("尚未列全", response.json()["turns"][-1]["message"])

    def test_read_tool_call_budget_rejects_excessive_batch(self):
        responses = [[("query_quotes", {"all_products": True}) for _ in range(33)]]
        with simulated_provider(responses) as (base, _), patch.dict("os.environ", {
            "QUOTELINK_API_KEY": "local-test", "QUOTELINK_MODEL": "fixture", "QUOTELINK_BASE_URL": base
        }), patch("procurement.agent.query_quotes") as query:
            response = self.ask("列出所有产品和报价")
            query.assert_not_called()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Inquiry.objects.exists())


class QuoteQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # 同一商品的多家报价、相似品名与停用报价用于检验查询边界。
        for code, supplier, product, price, active in [
            ("PAPER-01", "演示甲纸业", "A4打印纸", "25.50", True),
            ("PAPER-02", "演示乙纸业", "A4打印纸", "22.00", True),
            ("PAPER-03", "演示丙纸业", "A4打印纸", "22.00", True),
            ("A3-01", "演示甲纸业", "A3打印纸", "40.00", True),
            ("OFF-01", "停用纸业", "A4打印纸", "1.00", False),
        ]:
            Quote.objects.create(code=code, supplier=supplier, product=product, unit_price=price,
                                 freight="30", stock=60, lead_days=2, active=active)

    def test_normalized_product_and_stable_pagination(self):
        first = query_quotes(product=" ａ４ 打印纸 ", limit=2)
        self.assertEqual(first["query"]["product"], "A4打印纸")
        self.assertEqual(first["total"], 3)
        self.assertEqual([q["code"] for q in first["quotes"]], ["PAPER-02", "PAPER-03"])
        self.assertEqual(first["next_offset"], 2)
        second = query_quotes(product="A4打印纸", limit=2, offset=2)
        self.assertEqual([q["code"] for q in second["quotes"]], ["PAPER-01"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(set(second["quotes"][0]),
                         {"code", "supplier", "product", "unit_price", "freight", "stock", "lead_days", "arrival"})

    def test_ambiguous_and_missing_products(self):
        ambiguous = query_quotes(product="打印纸")
        self.assertEqual(ambiguous["status"], "ambiguous_product")
        self.assertEqual(set(ambiguous["matched_products"]), {"A4打印纸", "A3打印纸"})
        self.assertFalse(ambiguous["quotes"])
        missing = query_quotes(product="A4打印纸' OR 1=1 --")
        self.assertEqual(missing["status"], "not_found")
        self.assertFalse(missing["quotes"])

    def test_supplier_and_code_filters_are_combined(self):
        supplier = query_quotes(supplier="甲纸业")
        self.assertEqual(supplier["total"], 2)
        exact = query_quotes(product="A4打印纸", supplier="甲", code="paper-01")
        self.assertEqual(exact["total"], 1)
        self.assertEqual(exact["quotes"][0]["code"], "PAPER-01")
        self.assertEqual(query_quotes(supplier="乙", code="paper-01")["status"], "not_found")

    def test_query_rejects_empty_filters_and_unbounded_results(self):
        for args in ({}, {"product": " "}, {"supplier": "甲", "limit": 51},
                     {"code": "PAPER-01", "offset": -1}, {"code": "PAPER-01", "limit": True},
                     {"all_products": 1}, {"all_products": True, "product": "A4打印纸"}):
            with self.subTest(args=args), self.assertRaises(InputError):
                query_quotes(**args)

    def test_all_products_mode_is_paginated_and_excludes_inactive_quotes(self):
        first = query_quotes(all_products=True, limit=2)
        second = query_quotes(all_products=True, limit=2, offset=first["next_offset"])
        rows = first["quotes"] + second["quotes"]
        self.assertEqual(first["query"], {"all_products": True})
        self.assertEqual(first["total"], 4)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({q["code"] for q in rows}), 4)
        self.assertEqual({q["product"] for q in rows}, {"A4打印纸", "A3打印纸"})
        self.assertNotIn("OFF-01", {q["code"] for q in rows})
        self.assertIsNone(second["next_offset"])

    def test_all_products_mode_handles_empty_catalog(self):
        Quote.objects.update(active=False)
        result = query_quotes(all_products=True)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["quotes"])
        self.assertIsNone(result["next_offset"])


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
