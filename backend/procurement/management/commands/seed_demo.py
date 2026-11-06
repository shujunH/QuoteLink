from django.core.management.base import BaseCommand
from procurement.models import Quote


class Command(BaseCommand):
    help = "补齐三家虚构供应商的演示报价，保留已有报价修改"

    def handle(self, *args, **options):
        # 初始化链接中的 A/B/C 场景，同一命令可重复运行。
        samples = [
            ("QT-1001", "远川科技", 1100, 0, 12, 2),
            ("QT-1002", "青禾数码", 1160, 300, 20, 2),
            ("QT-1003", "北辰智采", 1190, 400, 30, 1),
        ]
        count = 0
        for code, name, price, freight, stock, days in samples:
            _, created = Quote.objects.get_or_create(code=code, defaults={
                "supplier": name, "product": "显示器", "unit_price": price,
                "freight": freight, "stock": stock, "lead_days": days})
            count += created
        self.stdout.write(self.style.SUCCESS(f"新增 {count} 条演示报价。"))
