# QUOTES - Centrální databáze nabídek

## Účel
Centrální uložiště všech nabídek od dodavatelů s možností filtrování, editace a exportu do Items.

## Datová struktura

### Quote (hlavička nabídky)

| Pole | Typ | Povinné | Default | Popis |
|------|-----|---------|---------|-------|
| **id** | CharField(64) | ✓ | auto | Primary key |
| **project** | FK→Project | ✗ | null | Odkaz na projekt (může být null) |
| **project_name** | CharField(255) | ✗ | '' | Název projektu (fallback když project=null) |
| **supplier_name** | CharField(255) | ✓ | - | Název dodavatele |
| **received_from** | CharField(255) | ✗ | '' | Kontaktní osoba dodavatele |
| **quote_number** | CharField(128) | ✓ | auto | Číslo nabídky (auto: SUPPLIER_YYYYMMDD_HHMM) |
| **create_date** | DateTimeField | ✓ | now | Datum vytvoření (automaticky) |
| **expire_date** | DateField | ✓ | - | Datum expirace |
| **expire_preset** | Integer | ✗ | null | Preset: 30/60/90/120/360 dní |
| **currency** | CharField(10) | ✗ | EUR | Měna |
| **shipping_cost** | Decimal(12,2) | ✗ | null | Náklady na dopravu |
| **incoterm** | CharField(50) | ✗ | '' | Dodací podmínky |
| **mov** | Decimal(12,2) | ✗ | null | Minimum Order Value |
| **extra_charge** | Decimal(12,2) | ✗ | null | Extra poplatky |
| **payment_terms** | CharField(255) | ✗ | '' | Platební podmínky |
| **packaging** | CharField(255) | ✗ | '' | Balení |
| **notes** | TextField | ✗ | '' | Poznámky |
| **attachment** | FileField | ✗ | null | Příloha (PDF, Excel, atd.) |
| **attachment_name** | CharField(255) | ✗ | auto | Název přílohy (auto z filename) |
| **source** | CharField(32) | ✓ | manual | Původ: manual/supplier_portal/email/import |
| **source_id** | CharField(64) | ✗ | '' | ID zdroje (např. SupplierAccess ID) |
| **created_by** | CharField(150) | ✗ | '' | Kdo vytvořil |
| **updated_at** | DateTimeField | ✓ | auto | Poslední změna |

### QuoteLine (řádky nabídky)

| Pole | Typ | Povinné | Default | Popis |
|------|-----|---------|---------|-------|
| **quote** | FK→Quote | ✓ | - | Odkaz na quote |
| **drawing_number** | CharField(255) | ✗ | '' | Výkres číslo (from system) |
| **manufacturer** | CharField(255) | ✗ | '' | Výrobce (from system) |
| **mpn** | CharField(255) | ✗ | '' | MPN (from system) |
| **description** | TextField | ✗ | '' | Popis |
| **uom** | CharField(20) | ✗ | pcs | Jednotka |
| **moq** | Integer | ✓ | 1 | Minimum Order Quantity |
| **manufacturing_lead_time** | CharField(100) | ✗ | '' | Výrobní doba |
| **supplier_lead_time** | CharField(100) | ✗ | 14 days | Dodací lhůta dodavatele |
| **available_stock** | Integer | ✗ | null | Dostupný sklad |
| **available_stock_date** | DateField | ✗ | null | Datum zachycení skladu |
| **qty_1..10** | CharField(50) | ✗ | '' | Množství tier 1-10 |
| **price_1..10** | Decimal(12,4) | ✗ | null | Cena tier 1-10 |
| **line_number** | Integer | ✗ | 0 | Pořadí řádku |
| **notes** | TextField | ✗ | '' | Poznámky k řádku |

## Logika automatických polí

### Quote Number
- Pokud prázdné → auto-generuje: `{SUPPLIER_NAME}_{YYYYMMDD_HHMM}`
- Příklad: `EATON_20260209_1430`
- Unikátní v celé DB

### Expire Date
- User může zadat datum ručně NEBO
- Vybrat preset (30/60/90/120/360 dní) → auto-vypočítá od create_date

### Attachment Name
- Pokud uživatel nahraje file ale nezadá název → auto z filename
- Uživatel může přepsat vlastním názvem

### MOQ
- Default = 1
- Pokud prázdné při save → nastav na 1

### Supplier Lead Time
- Default = "14 days"

### Available Stock Date
- Auto nastaví na dnešní datum když user zadá available_stock

## Budoucí napojení

### 1. Import ze Supplier Interaction (approval)
**Zdroj:** `supplier_access_approve()` endpoint v `views_api.py`

**Flow:**
1. Buyer schválí submission v Supplier Interaction
2. Systém vytvoří Quote záznam:
   - `source='supplier_portal'`
   - `source_id=access.id`
   - `supplier_name` z SupplierAccess
   - `project` z SupplierAccess
   - Quote-level fields z `submission_data` (currency, incoterms, etc.)
3. Systém vytvoří QuoteLine pro každý submitted item:
   - Drawing number, MPN, Manufacturer z requested_items
   - Prices z submission_data
   - MOQ, lead_time z submission_data

**Benefits:**
- Automatická archivace všech supplier submissions
- Historie cen v centrální DB
- Možnost srovnání cen napříč projekty

### 2. Manuální zadání z Items Detail
**Zdroj:** Items detail → Suppliers & Pricing section v `rfq.js`

**Flow:**
1. User otevře item detail, sekce Suppliers & Pricing
2. Zadá supplier name + ceny pro různé qty
3. Klikne "Save to Quotes" (nové tlačítko)
4. Frontend zavolá nový endpoint `/api/quotes/create_from_item`
5. Backend vytvoří Quote + QuoteLine

**UI změny potřeba:**
- Přidat tlačítko "💾 Save to Quotes" v Suppliers & Pricing
- Modal pro doplnění quote-level údajů (expire_date, quote_number, atd.)

### 3. Export z Quotes do Items
**Flow:**
1. User vybere quote line v Quotes view
2. Klikne "Export to Item"
3. Systém najde matching item v Project.data (drawing_no/MPN)
4. Přidá/updatuje supplier entry v item.suppliers[]
5. Refresh UI

### 4. Bulk import z Excel/CSV
**Flow:**
1. User nahraje Excel file v Quotes view
2. Systém parsuje columns → mapuje na QuoteLine fields
3. Vytvoří Quote + lines
4. Preview + confirm před save

## API Endpoints (budoucí implementace)

```python
# List & Filter
GET /api/quotes/
  ?project_id=...
  &supplier=...
  &expired=false
  &search=...

# Detail
GET /api/quotes/<quote_id>/

# Create
POST /api/quotes/
  {quote data + lines}

# Update
PUT /api/quotes/<quote_id>/

# Delete
DELETE /api/quotes/<quote_id>/

# Create from Item (Items detail integration)
POST /api/quotes/create_from_item/
  {item_data, supplier_name, prices, ...}

# Export to Items (Quotes → Items integration)
POST /api/quotes/<quote_id>/export_to_items/
  {selected_line_ids, project_id}
```

## UI Components

### Quotes List View (GLOBAL → Quotes)
**Layout:**
- Tabulka s řádky = Quotes
- Columns: Quote #, Supplier, Project, Items Count, Expire Date, Status (Active/Expired), Created
- Filtry: Project, Supplier, Date Range, Expired Yes/No
- Search: Quote #, Supplier name
- Actions: View Detail, Edit, Delete, Export

### Quote Detail View
**Sections:**
1. **Header:**
   - Quote #, Supplier, Project
   - Expire date (s indicator jestli expired)
   - Quote-level údaje (shipping, incoterm, MOV, etc.)
   - Attachment download

2. **Lines Table:**
   - Drawing #, MPN, Manufacturer
   - QTY/Price tiers (dynamicky 1-10 columns)
   - MOQ, Lead Time, Stock
   - Actions per line: Export to Item, Edit, Delete

3. **Footer:**
   - Notes
   - Metadata (Created by, Created at, Updated at, Source)

### Create/Edit Modal
**Tabs:**
1. **Quote Info** - header fields
2. **Items** - přidat/editovat lines
3. **Attachment** - nahrát file

## Styling
- Použít existující Enterprise CSS classes z `style.css`
- Table: `.enterprise-table`
- Buttons: `.rfq-btn-primary`, `.rfq-btn-secondary`
- Modal: `.rfq-modal`, `.rfq-modal-overlay`
- Filtry: konzistentní s ostatními views (RFQ Planner filter bar)
- Status badges: červená pro expired, zelená pro active

## Migration Notes

**Vytvoření migration:**
```bash
python manage.py makemigrations rfq
python manage.py migrate rfq
```

**Dependencies:**
- Žádné závislosti na existujících modelech (kromě Project FK)
- Quote může existovat bez Project (standalone quotes)

## Testing Checklist (budoucí)
- [ ] Auto-generate quote_number funguje správně
- [ ] Expire date calculation z presets
- [ ] MOQ default=1 při prázdné hodnotě
- [ ] Attachment name auto z filename
- [ ] FK na Project SET_NULL zachovává quote při smazání projektu
- [ ] QuoteLine qty/price tiers 1-10 ukládají správně
- [ ] as_dict() serialization funguje pro API
- [ ] Import ze Supplier Interaction vytváří správné záznamy
- [ ] Export do Items updatuje Project.data správně
