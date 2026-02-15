from django.conf import settings
from django.db import models


class Company(models.Model):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class UserCompanyProfile(models.Model):
    ROLE_SUPERADMIN = 'superadmin'
    ROLE_ADMIN = 'admin'
    ROLE_EDITOR = 'editor'
    ROLE_VIEWER = 'viewer'

    ROLE_CHOICES = [
        (ROLE_SUPERADMIN, 'SuperAdmin'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_EDITOR, 'Editor'),
        (ROLE_VIEWER, 'Viewer'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rfq_profile')
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='users', null=True, blank=True)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default=ROLE_VIEWER)
    is_management = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id}:{self.role}"


class Project(models.Model):
    """Stores a single RFQ project as a JSON blob (compatible with the existing JS app shape)."""

    id = models.CharField(primary_key=True, max_length=64)
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='projects', null=True, blank=True)
    name = models.CharField(max_length=255, blank=True, default='Untitled')
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Keep JSON blob consistent.
        if not isinstance(self.data, dict):
            self.data = {}
        self.data.setdefault('id', self.id)
        self.data.setdefault('name', self.name)
        # Prefer explicit name from payload if present.
        if 'name' in self.data and self.data['name']:
            self.name = str(self.data.get('name'))[:255]
        else:
            self.data['name'] = self.name
        super().save(*args, **kwargs)

    def as_dict(self):
        # Return the stored JSON blob; inject server timestamps for convenience.
        d = dict(self.data or {})
        d.setdefault('id', self.id)
        d.setdefault('name', self.name)
        d['server_updated_at'] = self.updated_at.isoformat()
        d['server_created_at'] = self.created_at.isoformat()
        return d

    class Meta:
        ordering = ['-updated_at']


class Attachment(models.Model):
    """File attachment linked to a project (project detail page)."""

    id = models.CharField(primary_key=True, max_length=64)
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='attachments', null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='attachments/')
    kind = models.CharField(max_length=32, blank=True, default='')  # e.g. 'cover' or 'file'
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def as_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'filename': self.file.name.split('/')[-1] if self.file else '',
            'url': self.file.url if self.file else '',
            'kind': self.kind or '',
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else '',
            'size': getattr(self.file, 'size', None),
        }

    class Meta:
        ordering = ['-uploaded_at']


class SupplierAccess(models.Model):
    """Manages token-based access for suppliers."""
    id = models.CharField(primary_key=True, max_length=64)
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='supplier_accesses', null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='supplier_accesses')
    supplier_name = models.CharField(max_length=255)
    requested_items = models.JSONField(default=list)
    submission_data = models.JSONField(default=dict, blank=True)
    
    # Status & Workflow
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('viewed', 'Viewed'),
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('re_quote_requested', 'Re-quote Requested'),
        ('rejected', 'Rejected'),
        ('lost', 'Lost'),
        ('expired', 'Expired'),
    ]
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='sent')
    rejection_reason = models.TextField(null=True, blank=True)
    round = models.IntegerField(default=1)
    
    # Timestamps & Meta
    created_at = models.DateTimeField(auto_now_add=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True) # kept for backward compat, same as submitted_at
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.CharField(max_length=150, null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    # Buyer contact info shown in portal
    contact_name = models.CharField(max_length=255, blank=True, default='')
    contact_email = models.EmailField(max_length=255, blank=True, default='')
    contact_phone = models.CharField(max_length=64, blank=True, default='')
    instruction_message = models.TextField(blank=True, default='')

    @property
    def is_editable(self):
        return self.status in ['sent', 'viewed', 're_quote_requested']

    def as_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'supplier_name': self.supplier_name,
            'status': self.status,
            'round': self.round,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'viewed_at': self.viewed_at.isoformat() if self.viewed_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else (self.replied_at.isoformat() if self.replied_at else None),
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'valid_until': self.valid_until.isoformat() if self.valid_until else None,
            'items_count': len(self.requested_items) if isinstance(self.requested_items, list) else 0,
            'requested_items': self.requested_items or [],
            'submission_data': self.submission_data or {},
            'rejection_reason': self.rejection_reason or '',
            'is_complete': self._check_completeness(),
            'reopen_requested': bool((self.submission_data or {}).get('reopen_requested')),
            'contact_name': self.contact_name or '',
            'contact_email': self.contact_email or '',
            'contact_phone': self.contact_phone or '',
            'instruction_message': self.instruction_message or '',
        }

    def _check_completeness(self):
        if not self.submission_data or 'items' not in self.submission_data:
            return False
        # Logic: count requested vs submitted prices
        try:
            req_count = len(self.requested_items)
            sub_items = self.submission_data.get('items', [])
            priced_count = sum(1 for i in sub_items if i.get('price'))
            return priced_count >= req_count and req_count > 0
        except Exception:
            return False

    class Meta:
        ordering = ['-created_at']


class SupplierAccessRound(models.Model):
    """
    Append-only history of negotiation rounds. 
    Created when a supplier submits (snapshot) OR when a buyer makes a decision.
    """
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='supplier_access_rounds', null=True, blank=True)
    supplier_access = models.ForeignKey(SupplierAccess, on_delete=models.CASCADE, related_name='rounds')
    round = models.IntegerField()
    requested_items = models.JSONField(default=list) # Snapshot of what was asked
    submission_data = models.JSONField(default=dict) # Snapshot of what was submitted
    submitted_at = models.DateTimeField(null=True, blank=True)
    
    # Decision Metadata
    buyer_decision = models.CharField(max_length=32, blank=True, null=True) # approved, rejected, re_quote, lost
    decision_reason = models.TextField(blank=True, null=True)
    decision_by = models.CharField(max_length=150, blank=True, null=True)
    decision_at = models.DateTimeField(blank=True, null=True)
    
    class Meta:
        ordering = ['round']


class SupplierInteractionFile(models.Model):
    """Secure file uploads linked to a specific round."""
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='supplier_interaction_files', null=True, blank=True)
    supplier_access = models.ForeignKey(SupplierAccess, on_delete=models.CASCADE, related_name='files')
    round = models.IntegerField()
    file = models.FileField(upload_to='interaction_files/%Y/%m/')
    original_name = models.CharField(max_length=255)
    size = models.IntegerField(default=0)
    uploaded_by = models.CharField(max_length=50) # 'supplier' or 'buyer'
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def as_dict(self):
        return {
            'id': self.id,
            'name': self.original_name,
            'size': self.size,
            'url': self.file.url if self.file else '',
            'uploaded_at': self.uploaded_at.isoformat(),
            'uploaded_by': self.uploaded_by
        }


# ============================================================================
# QUOTES - Centrální databáze nabídek od dodavatelů
# ============================================================================

class Quote(models.Model):
    """
    Centrální uložiště nabídek od dodavatelů.
    Napojení:
    - Import z Supplier Interaction (při approval)
    - Manuální zadání z Items Detail > Suppliers & Pricing
    - Export do Items jako supplier entry
    """

    # Identifikace
    id = models.CharField(primary_key=True, max_length=64)
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='quotes', null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='quotes')
    project_name = models.CharField(max_length=255, blank=True, default='')  # Fallback když project=None

    # Dodavatel & kontakt
    supplier_name = models.CharField(max_length=255)  # REQUIRED
    received_from = models.CharField(max_length=255, blank=True, default='')  # Contact name

    # Quote metadata
    quote_number = models.CharField(max_length=128, unique=True)  # REQUIRED nebo auto-generate (supplier+date)
    create_date = models.DateTimeField(auto_now_add=True)
    expire_date = models.DateField()  # REQUIRED

    EXPIRE_PRESET_CHOICES = [
        (30, '30 days'),
        (60, '60 days'),
        (90, '90 days'),
        (120, '120 days'),
        (360, '360 days'),
    ]
    expire_preset = models.IntegerField(choices=EXPIRE_PRESET_CHOICES, null=True, blank=True)

    # Quote-level údaje (volitelné)
    currency = models.CharField(max_length=10, blank=True, default='EUR')
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    incoterm = models.CharField(max_length=50, blank=True, default='')
    mov = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Minimum Order Value')
    extra_charge = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payment_terms = models.CharField(max_length=255, blank=True, default='')
    packaging = models.CharField(max_length=255, blank=True, default='')

    # Poznámky & přílohy
    notes = models.TextField(blank=True, default='')
    attachment = models.FileField(upload_to='quotes/%Y/%m/', null=True, blank=True)
    attachment_name = models.CharField(max_length=255, blank=True, default='')

    # Tracking
    created_by = models.CharField(max_length=150, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    # Původ quote (pro tracking)
    SOURCE_CHOICES = [
        ('manual', 'Manual Entry'),
        ('supplier_portal', 'Supplier Portal'),
        ('email', 'Email'),
        ('import', 'Import'),
        ('rfq_planner', 'RFQ Planner'),
    ]
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default='manual')
    source_id = models.CharField(max_length=64, blank=True, default='', help_text='SupplierAccess ID pokud ze Supplier Portalu')

    def save(self, *args, **kwargs):
        # Auto-generate quote_number if empty: SUPPLIER_YYYYMMDD_HHMM
        if not self.quote_number:
            from datetime import datetime
            supplier_slug = self.supplier_name.replace(' ', '_')[:20]
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')
            self.quote_number = f'{supplier_slug}_{timestamp}'

        # Auto-generate attachment_name if file exists but name is empty
        if self.attachment and not self.attachment_name:
            self.attachment_name = self.attachment.name.split('/')[-1]

        # Set project_name from project if available
        if self.project and not self.project_name:
            self.project_name = self.project.name

        super().save(*args, **kwargs)

    def as_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'project_name': self.project_name,
            'supplier_name': self.supplier_name,
            'received_from': self.received_from,
            'quote_number': self.quote_number,
            'create_date': self.create_date.isoformat() if self.create_date else None,
            'expire_date': self.expire_date.isoformat() if self.expire_date else None,
            'expire_preset': self.expire_preset,
            'currency': self.currency,
            'shipping_cost': float(self.shipping_cost) if self.shipping_cost else None,
            'incoterm': self.incoterm,
            'mov': float(self.mov) if self.mov else None,
            'extra_charge': float(self.extra_charge) if self.extra_charge else None,
            'payment_terms': self.payment_terms,
            'packaging': self.packaging,
            'notes': self.notes,
            'attachment_url': self.attachment.url if self.attachment else '',
            'attachment_name': self.attachment_name,
            'created_by': self.created_by,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'source': self.source,
            'source_id': self.source_id,
            'lines_count': self.lines.count(),
        }

    class Meta:
        ordering = ['-create_date']


class QuoteLine(models.Model):
    """
    Řádky nabídky - jednotlivé itemy s cenami a qty tiers.
    Podporuje dynamické qty/price tiers 1-10 (konzistentní se zbytkem systému).
    """

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='lines')

    # Item identifikace (from system)
    drawing_number = models.CharField(max_length=255, blank=True, default='')
    manufacturer = models.CharField(max_length=255, blank=True, default='')
    mpn = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    uom = models.CharField(max_length=20, blank=True, default='pcs')

    # Supplier-specific údaje
    moq = models.IntegerField(default=1)  # REQUIRED, default=1
    manufacturing_lead_time = models.CharField(max_length=100, blank=True, default='')
    supplier_lead_time = models.CharField(max_length=100, blank=True, default='14 days')  # Default 14 dní

    # Stock info (volitelné, s datem zachycení)
    available_stock = models.IntegerField(null=True, blank=True)
    available_stock_date = models.DateField(null=True, blank=True, auto_now_add=False)

    # Dynamické QTY/PRICE tiers (1-10, konzistentní s RFQ Planner)
    # qty_1 je from system (item detail quantities), supplier může přidat další
    qty_1 = models.CharField(max_length=50, blank=True, default='')
    price_1 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_2 = models.CharField(max_length=50, blank=True, default='')
    price_2 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_3 = models.CharField(max_length=50, blank=True, default='')
    price_3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_4 = models.CharField(max_length=50, blank=True, default='')
    price_4 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_5 = models.CharField(max_length=50, blank=True, default='')
    price_5 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_6 = models.CharField(max_length=50, blank=True, default='')
    price_6 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_7 = models.CharField(max_length=50, blank=True, default='')
    price_7 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_8 = models.CharField(max_length=50, blank=True, default='')
    price_8 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_9 = models.CharField(max_length=50, blank=True, default='')
    price_9 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    qty_10 = models.CharField(max_length=50, blank=True, default='')
    price_10 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    # Line metadata
    line_number = models.IntegerField(default=0)  # Pořadí v quote
    notes = models.TextField(blank=True, default='')

    def as_dict(self):
        data = {
            'id': self.id,
            'quote_id': self.quote_id,
            'drawing_number': self.drawing_number,
            'manufacturer': self.manufacturer,
            'mpn': self.mpn,
            'description': self.description,
            'uom': self.uom,
            'moq': self.moq,
            'manufacturing_lead_time': self.manufacturing_lead_time,
            'supplier_lead_time': self.supplier_lead_time,
            'available_stock': self.available_stock,
            'available_stock_date': self.available_stock_date.isoformat() if self.available_stock_date else None,
            'line_number': self.line_number,
            'notes': self.notes,
        }

        # Add dynamic qty/price tiers
        for i in range(1, 11):
            qty_val = getattr(self, f'qty_{i}', '')
            price_val = getattr(self, f'price_{i}', None)
            data[f'qty_{i}'] = qty_val
            data[f'price_{i}'] = float(price_val) if price_val is not None else None

        return data

    class Meta:
        ordering = ['line_number', 'id']


class ProjectAccess(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='access_entries')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='project_access_entries')
    can_view = models.BooleanField(default=True)
    can_edit = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='project_access_granted')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('project', 'user')]


class EditLock(models.Model):
    resource_key = models.CharField(max_length=255, unique=True, db_index=True)
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='edit_locks')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='edit_locks')
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='edit_locks')
    locked_by_display = models.CharField(max_length=255, blank=True, default='')
    context = models.CharField(max_length=64, blank=True, default='')
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AuditLog(models.Model):
    company = models.ForeignKey('Company', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    actor_role = models.CharField(max_length=32, blank=True, default='')
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64, blank=True, default='')
    entity_id = models.CharField(max_length=128, blank=True, default='')
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    metadata_json = models.JSONField(default=dict, blank=True)
    ip = models.CharField(max_length=64, blank=True, default='')
    user_agent = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
