# Quotes Migrations - Návod

## 1. Vytvoř migrations

```bash
python manage.py makemigrations rfq
```

**Očekávaný výstup:**
```
Migrations for 'rfq':
  rfq\migrations\0XXX_quote_quoteline.py
    - Create model Quote
    - Create model QuoteLine
```

## 2. Zkontroluj vygenerovanou migration

Otevři soubor `rfq/migrations/0XXX_quote_quoteline.py` a zkontroluj:
- ✅ Quote model má všechny fieldy
- ✅ QuoteLine model má všechny fieldy včetně qty_1..10, price_1..10
- ✅ FK z QuoteLine na Quote je správně
- ✅ FK z Quote na Project má `on_delete=models.SET_NULL`

## 3. Spusť migration

```bash
python manage.py migrate rfq
```

**Očekávaný výstup:**
```
Running migrations:
  Applying rfq.0XXX_quote_quoteline... OK
```

## 4. Ověř v databázi

**Pomocí Django shell:**
```bash
python manage.py shell
```

```python
from rfq.models import Quote, QuoteLine
from datetime import date, timedelta

# Test vytvoření quote
q = Quote.objects.create(
    id='test-001',
    supplier_name='Test Supplier',
    expire_date=date.today() + timedelta(days=30),
    currency='USD',
    notes='Test quote'
)
print(q.quote_number)  # Should auto-generate: Test_Supplier_YYYYMMDD_HHMM

# Test vytvoření line
line = QuoteLine.objects.create(
    quote=q,
    drawing_number='TEST-001',
    mpn='TEST-MPN',
    qty_1='100',
    price_1=12.50
)
print(line.as_dict())

# Cleanup
q.delete()
```

## 5. Testuj API endpoints

**List quotes:**
```bash
curl http://localhost:8000/rfq/api/quotes/
```

**Create quote:**
```bash
curl -X POST http://localhost:8000/rfq/api/quotes/create/ \
  -H "Content-Type: application/json" \
  -d '{
    "supplier_name": "EATON",
    "expire_date": "2026-03-10",
    "currency": "EUR",
    "lines": [
      {
        "drawing_number": "5240KLM-81",
        "manufacturer": "EATON",
        "mpn": "121734",
        "qty_1": "7",
        "price_1": 25.50,
        "moq": 1
      }
    ]
  }'
```

## 6. Zkontroluj Django Admin

Otevři admin interface:
```
http://localhost:8000/admin/rfq/quote/
http://localhost:8000/admin/rfq/quoteline/
```

Měly by být viditelné Quote a QuoteLine tabulky s inline editací.

## Troubleshooting

### Chyba: "no such table: rfq_quote"
→ Zapomněls spustit `migrate`, běž zpět na krok 3

### Chyba: "Duplicate column name"
→ Migrations už byly spuštěny dříve, zkontroluj `python manage.py showmigrations rfq`

### Chyba: "IntegrityError: NOT NULL constraint"
→ Zkontroluj že `expire_date` je poskytnutý při vytváření Quote

### Auto-generate quote_number nefunguje
→ Quote.save() má auto-generate logiku, zkontroluj že `quote_number` je prázdný string (ne None)
