from django.contrib import admin
from .models import Quote

admin.site.site_header = "采价通 · 采购管理"
admin.site.site_title = "采价通"

@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("code", "product", "supplier", "unit_price", "freight", "stock", "lead_days", "active")
    list_filter = ("active", "product")
    search_fields = ("code", "supplier", "product")
