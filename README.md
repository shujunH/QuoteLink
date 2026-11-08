# 采价通 · QuoteLink

用自然语言发起采购询价，比较同一商品的供应商报价，并在确认后生成采购申请草稿。Vue 3 前端通过 JSON API 访问 Django 5.2，业务数据保存在 SQLite。此版本处理单品、单供应商整单交付，三家供应商均为虚构演示数据。

## 在 Windows 本地启动

推荐 Python 3.12 与 Node.js 18.20 或 22。在项目根目录打开两个 PowerShell 窗口。首次启动后，后续只需分别执行后端的 `runserver` 和前端的 `npm.cmd run dev`。

**窗口一，Django 后端**

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8010
```

**窗口二，Vue 前端**

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

打开 [采价通工作台](http://127.0.0.1:5174/)。Vite 将 API 和管理后台请求转发到 Django。询价历史和草稿按浏览器会话隔离，请用同一个地址和浏览器回到工作区。

## 演示一次完整询价

1. 在工作台试问「什么是运费成本？」或「采购品类有哪些？」，再问「平台有哪些商品可以询价？」查看有效报价对应的品名。随后发送「下周三前要到货20台显示器，总预算2.4万元，含运费」发起比价，也可以直接选择「20 台显示器采购」。
2. 展开工具调用记录，查看查询参数与三份核算结果。预算 24,000 元时，青禾数码（QT-1002）满足全部条件，含运费总价为 1,160 × 20 + 300 = **23,500 元**。远川科技缺 8 台，北辰智采含运费超预算 200 元。
3. 输入「预算改成 2.3 万元，其他条件不变」，此时没有可行报价。再输入「预算改成 23500 元」即可演示预算刚好达标。
4. 追问「刚才推荐的那家收多少运费？」，然后在原推荐卡片点击「确认并保存草稿」。重复确认同一轮只会返回同一份草稿。
5. 要演示报价变更，先在后端目录执行 `.\.venv\Scripts\python.exe manage.py createsuperuser`，从报价库的「管理报价」进入 Django Admin。得到推荐后、确认前把青禾的单价改为 1,161 元，确认会提示重新比价。测试后可在后台恢复原价。重复执行 `seed_demo` 只会补齐缺失的报价。

## 切换真实模型

默认使用本地规则演示，支持示例中的固定采购问答、报价解释、修改预算或数量，以及“明天”“下周三”和 ISO 日期。开放式知识问答需要启用模型。工作台顶部会显示当前模式。

要启用模型，在后端目录将 `.env.example` 复制为 `.env`，配置后重启 Django。模型服务需兼容 Chat Completions 并支持 function calling。

| 后端变量 | 用途 |
| --- | --- |
| QUOTELINK_API_KEY | 模型服务密钥。留空使用本地规则 |
| QUOTELINK_MODEL | 服务商提供的模型标识 |
| QUOTELINK_BASE_URL | 兼容服务的 API 根地址，比如服务商文档中的 v1 地址。使用客户端默认地址时留空 |

模型接收本轮消息、当前采购条件及最近四轮对话，可直接回答采购知识。平台品名来自 `list_products`。启用模型后，可直接问“告诉我 A4打印纸有哪些供应商和报价”或“青禾数码目前有哪些报价”，由只读工具 `query_quotes` 查询数据库当前有效报价，返回单价、整单运费、库存和交付天数，无需先提供数量、预算和到货日。查询支持按品名、供应商或报价编号筛选，每页最多 50 条；品名空格、大小写会规范处理，匹配多个品名时先澄清。查询不改变当前采购条件，也不生成新的可确认推荐。数据库记录的是供应商，不是独立的生产厂家资料。

解释“刚才推荐的那家”等历史比价问题时，`read_comparison` 读取该会话最近一次询价的快照；查看当前报价则使用 `query_quotes`，后台改价后再次查询可得到新价格。实际比价仍通过 `find_quotes` 提取变更、读取报价，并通过 `evaluate_quote` 核算各候选。Django 根据核算结果生成推荐。问答计入对话历史，草稿始终关联实际比价轮次。模型调用失败时接口返回错误，失败轮次不会写入会话。

## 后端实现与验证

- `backend/procurement/agent.py` 负责两种输入模式、有限轮次的工具调用和调用记录。
- `backend/procurement/pricing.py` 负责需求校验、Decimal 金额计算、数量与交期判断，以及最低可行报价的选择。演示交期按询价日加自然日计算。
- `backend/procurement/views.py` 在事务内复查当前推荐和报价快照，用会话轮次及数据库唯一约束处理过期提交与重复确认。
- `GET /api/workspace/` 初始化会话及 CSRF Cookie。使用同一会话访问 `POST /api/chat/`、`GET /api/inquiries/<id>/` 和 `POST /api/inquiries/<id>/confirm/`。写请求同时传入 `X-CSRFToken`。续问提交 `message`、`inquiry_id`、`revision`，确认提交 `revision` 和推荐的 `code`。
- 在后端目录执行 `.\.venv\Scripts\python.exe manage.py test`。测试含价格与交期边界、连续询价、会话隔离、确认前变价和重复确认，并通过本地模拟服务验证 LangChain 工具调用协议。
- 在前端目录执行 `npm.cmd run build` 检查生产构建。
