import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

class Quote(models.Model):
    """一个供应商针对一种商品给出的整单报价。"""
    code = models.CharField("报价编号", max_length=32, primary_key=True)
    supplier = models.CharField("供应商", max_length=80)
    product = models.CharField("品名", max_length=80)
    unit_price = models.DecimalField("单价", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    freight = models.DecimalField("整单运费", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    stock = models.PositiveIntegerField("可供数量")
    lead_days = models.PositiveIntegerField("交付天数")
    active = models.BooleanField("有效", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = verbose_name_plural = "供应商报价"

    def __str__(self):
        return f"{self.code} · {self.supplier} · {self.product}"


class Inquiry(models.Model):
    """保存浏览器会话下的多轮需求和每轮可追溯的计算结果。"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.CharField(max_length=64, db_index=True)
    revision = models.PositiveIntegerField(default=0)
    needs = models.JSONField(default=dict)
    turns = models.JSONField(default=list)
    updated = models.DateTimeField(auto_now=True)


class Draft(models.Model):
    """冻结用户确认时的采购条件和报价，每轮最多一份。"""
    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField()
    needs = models.JSONField()
    quote = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["inquiry", "revision"], name="unique_draft_per_revision")]
