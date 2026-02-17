import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from .api_common import (
    audit_log as _audit_log,
    get_buyer_username as _get_buyer_username,
    json_body as _json_body,
    require_auth_and_profile as _require_auth_and_profile,
    require_role as _require_role,
    require_same_origin_for_unsafe as _require_same_origin_for_unsafe,
)
from .models import Project, Quote, QuoteLine, SupplierAccess

logger = logging.getLogger(__name__)

def _projects_qs_for_actor(actor):
    qs = Project.objects.all()
    if actor and actor.get('is_superadmin'):
        scope_company = actor.get('scope_company')
        return qs.filter(company=scope_company) if scope_company else qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)
def _quotes_qs_for_actor(actor):
    qs = Quote.objects.select_related('project')
    if actor and actor.get('is_superadmin'):
        scope_company = actor.get('scope_company')
        return qs.filter(company=scope_company) if scope_company else qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)
def _portal_qs_for_actor(actor):
    qs = SupplierAccess.objects.select_related('project').filter(status__in=['submitted', 'approved', 're_quote_requested'])
    if actor and actor.get('is_superadmin'):
        scope_company = actor.get('scope_company')
        return qs.filter(company=scope_company) if scope_company else qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)
def _normalize_name(s):
    return ' '.join(str(s).split()).strip()
def _safe_int(v, default=0):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default
def _safe_decimal(v):
    if v is None or v == '':
        return None
    try:
        return Decimal(str(v).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return None
def _parse_val_decimal(val):
    if val is None or val == '':
        return None
    try:
        return Decimal(str(val).replace(',', '.').strip())
    except (ValueError, InvalidOperation):
        return None
def quotes_list(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    from datetime import date, datetime
    from django.db.models import Q
    from django.utils import timezone
    manual_qs = _quotes_qs_for_actor(actor)
    portal_qs = _portal_qs_for_actor(actor)
    project_id = request.GET.get('project_id')
    if project_id:
        manual_qs = manual_qs.filter(project_id=project_id)
        portal_qs = portal_qs.filter(project_id=project_id)
    supplier = request.GET.get('supplier', '').strip()
    if supplier:
        manual_qs = manual_qs.filter(supplier_name__icontains=supplier)
        portal_qs = portal_qs.filter(supplier_name__icontains=supplier)
    expired_param = request.GET.get('expired', '').strip().lower()
    today = date.today()
    now = timezone.now()
    if expired_param == 'true':
        manual_qs = manual_qs.filter(expire_date__lt=today)
        portal_qs = portal_qs.filter(Q(valid_until__lt=now) | Q(status__in=['expired', 'lost', 'rejected']))
    elif expired_param == 'false':
        manual_qs = manual_qs.filter(expire_date__gte=today)
        portal_qs = portal_qs.exclude(Q(valid_until__lt=now) | Q(status__in=['expired', 'lost', 'rejected']))
    search = request.GET.get('search', '').strip()
    if search:
        manual_qs = manual_qs.filter(
            Q(quote_number__icontains=search)
            | Q(supplier_name__icontains=search)
            | Q(project_name__icontains=search)
            | Q(received_from__icontains=search)
        )
        portal_qs = portal_qs.filter(Q(supplier_name__icontains=search) | Q(project__name__icontains=search))
    results = []
    for q in manual_qs:
        d = q.as_dict()
        d['source_type'] = 'manual'
        d['status'] = 'expired' if (q.expire_date and q.expire_date < today) else 'active'
        results.append(d)
    for p in portal_qs:
        sub = p.submission_data or {}
        q_num = sub.get('quote_number') or f'PORTAL-{p.round}'
        exp_date = p.valid_until
        if not exp_date and sub.get('quote_valid_until'):
            try:
                exp_date = datetime.fromisoformat(str(sub.get('quote_valid_until')))
            except Exception:
                pass
        items = sub.get('items') or p.requested_items or []
        lines_count = len(items) if isinstance(items, list) else 0
        results.append({
            'id': p.id,
            'project_id': p.project_id,
            'project_name': p.project.name if p.project else '',
            'supplier_name': p.supplier_name,
            'received_from': p.contact_name or sub.get('supplier_contact_name') or '',
            'quote_number': q_num,
            'create_date': p.created_at.isoformat() if p.created_at else None,
            'expire_date': exp_date.isoformat() if exp_date else None,
            'currency': sub.get('currency') or 'EUR',
            'status': p.status,
            'source_type': 'portal',
            'lines_count': lines_count,
            'source_id': p.id,
        })
    results.sort(key=lambda x: str(x.get('create_date') or ''), reverse=True)
    try:
        limit = int(request.GET.get('limit', 100))
        offset = int(request.GET.get('offset', 0))
        limit = min(limit, 500)
    except (ValueError, TypeError):
        limit = 100
        offset = 0
    total = len(results)
    page_items = results[offset:offset + limit]
    return JsonResponse({'ok': True, 'quotes': page_items, 'total': total, 'limit': limit, 'offset': offset})
def quotes_detail(request, quote_id):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    try:
        quote = _quotes_qs_for_actor(actor).get(id=quote_id)
        data = quote.as_dict()
        data['lines'] = [line.as_dict() for line in quote.lines.all()]
        data['source_type'] = 'manual'
        return JsonResponse({'ok': True, 'quote': data})
    except Quote.DoesNotExist:
        pass
    try:
        access = _portal_qs_for_actor(actor).get(id=quote_id)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Quote not found'}, status=404)
    sub = access.submission_data or {}
    sub_items = sub.get('items') or []
    req_items = access.requested_items or []
    req_by_id = {}
    req_by_dn = {}
    if isinstance(req_items, list):
        for ri in req_items:
            if not isinstance(ri, dict):
                continue
            rid = str(ri.get('id') or '').strip()
            rdn = (ri.get('item_drawing_no') or ri.get('drawing_no') or '').strip().lower()
            rmpn = (ri.get('mpn') or '').strip().lower()
            if rid:
                req_by_id[rid] = ri
            if rdn:
                req_by_dn[rdn] = ri
            if rmpn:
                req_by_dn[rmpn] = ri
    merged_items = []
    if isinstance(sub_items, list) and sub_items:
        for si in sub_items:
            if not isinstance(si, dict):
                continue
            sid = str(si.get('id') or '').strip()
            sdn = (si.get('item_drawing_no') or si.get('drawing_no') or '').strip().lower()
            smpn = (si.get('mpn') or '').strip().lower()
            ri = req_by_id.get(sid)
            if not ri and sdn:
                ri = req_by_dn.get(sdn)
            if not ri and smpn:
                ri = req_by_dn.get(smpn)
            ri = ri or {}
            merged_items.append({**ri, **si})
    elif isinstance(req_items, list) and req_items:
        merged_items = [ri for ri in req_items if isinstance(ri, dict)]
    lines = []
    for idx, item in enumerate(merged_items):
        line = {
            'id': item.get('id') or f'portal-item-{idx}',
            'drawing_number': item.get('item_drawing_no') or item.get('drawing_no') or '',
            'manufacturer': item.get('manufacturer') or '',
            'mpn': item.get('mpn') or '',
            'description': item.get('description') or '',
            'uom': item.get('uom') or 'pcs',
            'moq': item.get('moq') or 1,
            'supplier_lead_time': item.get('lead_time') or item.get('supplier_lead_time') or '',
            'notes': item.get('notes') or item.get('comment') or '',
            'line_number': idx + 1,
        }
        for i in range(1, 11):
            line[f'qty_{i}'] = item.get(f'qty_{i}') or (item.get('qty') if i == 1 else '')
            val = item.get(f'price_{i}')
            if val is None and i == 1:
                val = item.get('price')
            if val is not None and str(val).strip():
                try:
                    line[f'price_{i}'] = float(val)
                except Exception:
                    line[f'price_{i}'] = None
            else:
                line[f'price_{i}'] = None
        lines.append(line)
    data = {
        'id': access.id,
        'project_id': access.project_id,
        'project_name': access.project.name if access.project else '',
        'supplier_name': access.supplier_name,
        'quote_number': sub.get('quote_number') or f'PORTAL-{access.round}',
        'create_date': access.created_at.isoformat() if access.created_at else None,
        'expire_date': access.valid_until.isoformat() if access.valid_until else None,
        'currency': sub.get('currency') or 'EUR',
        'status': access.status,
        'source_type': 'portal',
        'lines': lines,
        'incoterm': sub.get('incoterms') or '',
        'payment_terms': sub.get('payment_terms') or '',
        'shipping_cost': sub.get('shipping') or sub.get('shipping_cost'),
        'packaging': sub.get('packaging') or '',
        'notes': sub.get('notes') or '',
        'received_from': access.contact_name or sub.get('supplier_contact_name') or '',
    }
    return JsonResponse({'ok': True, 'quote': data})
def quotes_create(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    supplier_name = data.get('supplier_name', '').strip()
    expire_date_str = data.get('expire_date', '').strip()
    if not supplier_name:
        return JsonResponse({'error': 'supplier_name is required'}, status=400)
    if not expire_date_str:
        return JsonResponse({'error': 'expire_date is required'}, status=400)
    from datetime import datetime, timedelta
    try:
        expire_date = datetime.strptime(expire_date_str, '%Y-%m-%d').date()
    except ValueError:
        expire_preset = data.get('expire_preset')
        if expire_preset in [30, 60, 90, 120, 360]:
            expire_date = (datetime.now() + timedelta(days=expire_preset)).date()
        else:
            return JsonResponse({'error': 'Invalid expire_date format (use YYYY-MM-DD or expire_preset)'}, status=400)
    quote_id = data.get('id') or str(uuid.uuid4())
    project_id = data.get('project_id')
    project_obj = None
    if project_id:
        project_obj = _projects_qs_for_actor(actor).filter(id=str(project_id)).first()
        if not project_obj:
            return JsonResponse({'error': 'Project not found or access denied'}, status=404)
    with transaction.atomic():
        quote = Quote(
            id=quote_id,
            company=project_obj.company if project_obj else actor.get('company'),
            project=project_obj,
            project_name=(project_obj.name if project_obj else data.get('project_name', '')),
            supplier_name=supplier_name,
            received_from=data.get('received_from', ''),
            quote_number=data.get('quote_number', ''),
            expire_date=expire_date,
            expire_preset=data.get('expire_preset'),
            currency=data.get('currency', 'EUR'),
            shipping_cost=data.get('shipping_cost'),
            incoterm=data.get('incoterm', ''),
            mov=data.get('mov'),
            extra_charge=data.get('extra_charge'),
            payment_terms=data.get('payment_terms', ''),
            packaging=data.get('packaging', ''),
            notes=data.get('notes', ''),
            attachment_name=data.get('attachment_name', ''),
            created_by=data.get('created_by', _get_buyer_username(request)),
            source=data.get('source', 'manual'),
            source_id=data.get('source_id', ''),
        )
        quote.save()
        for idx, line_data in enumerate(data.get('lines', [])):
            line = QuoteLine(
                quote=quote,
                drawing_number=line_data.get('drawing_number', ''),
                manufacturer=line_data.get('manufacturer', ''),
                mpn=line_data.get('mpn', ''),
                description=line_data.get('description', ''),
                uom=line_data.get('uom', 'pcs'),
                moq=line_data.get('moq', 1),
                manufacturing_lead_time=line_data.get('manufacturing_lead_time', ''),
                supplier_lead_time=line_data.get('supplier_lead_time', '14 days'),
                available_stock=line_data.get('available_stock'),
                available_stock_date=line_data.get('available_stock_date'),
                line_number=idx + 1,
                notes=line_data.get('notes', ''),
            )
            for i in range(1, 11):
                setattr(line, f'qty_{i}', line_data.get(f'qty_{i}', ''))
                setattr(line, f'price_{i}', line_data.get(f'price_{i}'))
            line.save()
    _audit_log(request, actor, action='quote.create', entity_type='quote', entity_id=quote.id, project=quote.project, metadata={'quote_number': quote.quote_number})
    return JsonResponse({'ok': True, 'quote': quote.as_dict()}, status=201)
def quotes_update(request, quote_id):
    if request.method not in ('PUT', 'POST'):
        return HttpResponseNotAllowed(['PUT', 'POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    try:
        quote = _quotes_qs_for_actor(actor).get(id=quote_id)
    except Quote.DoesNotExist:
        return JsonResponse({'error': 'Quote not found'}, status=404)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    from datetime import datetime
    with transaction.atomic():
        if 'supplier_name' in data:
            quote.supplier_name = data['supplier_name']
        if 'received_from' in data:
            quote.received_from = data['received_from']
        if 'quote_number' in data:
            quote.quote_number = data['quote_number']
        if 'expire_date' in data:
            try:
                quote.expire_date = datetime.strptime(data['expire_date'], '%Y-%m-%d').date()
            except ValueError:
                pass
        if 'expire_preset' in data:
            quote.expire_preset = data['expire_preset']
        if 'currency' in data:
            quote.currency = data['currency']
        if 'shipping_cost' in data:
            quote.shipping_cost = data['shipping_cost']
        if 'incoterm' in data:
            quote.incoterm = data['incoterm']
        if 'mov' in data:
            quote.mov = data['mov']
        if 'extra_charge' in data:
            quote.extra_charge = data['extra_charge']
        if 'payment_terms' in data:
            quote.payment_terms = data['payment_terms']
        if 'packaging' in data:
            quote.packaging = data['packaging']
        if 'notes' in data:
            quote.notes = data['notes']
        if 'project_id' in data:
            npid = data['project_id']
            if npid:
                nproj = _projects_qs_for_actor(actor).filter(id=str(npid)).first()
                if not nproj:
                    return JsonResponse({'error': 'Project not found or access denied'}, status=404)
                quote.project = nproj
                quote.company = nproj.company
            else:
                quote.project = None
        if 'project_name' in data:
            quote.project_name = data['project_name']
        quote.save()
        if 'lines' in data:
            quote.lines.all().delete()
            for idx, line_data in enumerate(data['lines']):
                line = QuoteLine(
                    quote=quote,
                    drawing_number=line_data.get('drawing_number', ''),
                    manufacturer=line_data.get('manufacturer', ''),
                    mpn=line_data.get('mpn', ''),
                    description=line_data.get('description', ''),
                    uom=line_data.get('uom', 'pcs'),
                    moq=line_data.get('moq', 1),
                    manufacturing_lead_time=line_data.get('manufacturing_lead_time', ''),
                    supplier_lead_time=line_data.get('supplier_lead_time', '14 days'),
                    available_stock=line_data.get('available_stock'),
                    available_stock_date=line_data.get('available_stock_date'),
                    line_number=idx + 1,
                    notes=line_data.get('notes', ''),
                )
                for i in range(1, 11):
                    setattr(line, f'qty_{i}', line_data.get(f'qty_{i}', ''))
                    setattr(line, f'price_{i}', line_data.get(f'price_{i}'))
                line.save()
    _audit_log(request, actor, action='quote.update', entity_type='quote', entity_id=quote.id, project=quote.project, metadata={'quote_number': quote.quote_number})
    return JsonResponse({'ok': True, 'quote': quote.as_dict()})
def quotes_delete(request, quote_id):
    if request.method != 'DELETE':
        return HttpResponseNotAllowed(['DELETE'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'admin'):
        return JsonResponse({'error': 'Admin permission required'}, status=403)
    try:
        quote = _quotes_qs_for_actor(actor).get(id=quote_id)
    except Quote.DoesNotExist:
        return JsonResponse({'error': 'Quote not found'}, status=404)
    quote_number = quote.quote_number
    quote_id_val = quote.id
    proj = quote.project
    quote.delete()
    _audit_log(request, actor, action='quote.delete', entity_type='quote', entity_id=quote_id_val, project=proj, metadata={'quote_number': quote_number})
    return JsonResponse({'ok': True, 'deleted': quote_number})
def quotes_create_from_item(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    from datetime import timedelta
    from django.utils import timezone
    try:
        data = json.loads(request.body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    pid = data.get('project_id')
    item_id = data.get('item_id')
    sname = (data.get('supplier_name') or '').strip()
    if not pid or not item_id or not sname:
        return JsonResponse({'error': 'Missing required fields (project_id, item_id, supplier_name)'}, status=400)
    try:
        proj = _projects_qs_for_actor(actor).get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)
    target_item = None
    for it in (proj.data or {}).get('items') or []:
        if str(it.get('id') or '') == str(item_id):
            target_item = it
            break
    if not target_item:
        return JsonResponse({'error': 'Item not found in project'}, status=404)
    now = timezone.now()
    user = _get_buyer_username(request)
    quote = Quote.objects.create(
        id=str(uuid.uuid4()),
        project=proj,
        project_name=proj.name,
        supplier_name=sname,
        source='manual',
        source_id='item_detail',
        created_by=user,
        expire_date=(now + timedelta(days=30)).date(),
        currency=str(data.get('currency') or 'EUR'),
        quote_number=f"{_normalize_name(sname)[:15]}_{now.strftime('%Y%m%d_%H%M')}",
    )
    line = QuoteLine(
        quote=quote,
        drawing_number=target_item.get('drawing_no') or target_item.get('item_drawing_no') or '',
        manufacturer=target_item.get('manufacturer') or '',
        mpn=target_item.get('mpn') or '',
        description=target_item.get('description') or '',
        uom=target_item.get('uom') or 'pcs',
        line_number=1,
    )
    line.qty_1 = str(data.get('qty') or data.get('qty_1') or '')
    line.price_1 = _parse_val_decimal(data.get('price') or data.get('price_1'))
    line.moq = int(_parse_val_decimal(data.get('moq')) or 1)
    line.supplier_lead_time = str(data.get('lead_time') or '')
    line.notes = str(data.get('notes') or '')
    for i in range(2, 11):
        setattr(line, f'qty_{i}', str(data.get(f'qty_{i}') or ''))
        setattr(line, f'price_{i}', _parse_val_decimal(data.get(f'price_{i}')))
    line.save()
    return JsonResponse({'ok': True, 'quote_id': quote.id, 'line_id': line.id})
def quotes_export_to_item(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    from django.utils import timezone
    try:
        data = json.loads(request.body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    pid = data.get('project_id')
    line_ids = data.get('line_ids') or []
    if not line_ids and data.get('line_id'):
        line_ids = [data.get('line_id')]
    if not pid or not line_ids:
        return JsonResponse({'error': 'Missing project_id or line_ids'}, status=400)
    try:
        proj = _projects_qs_for_actor(actor).get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project Not Found'}, status=404)
    try:
        quote_lines = QuoteLine.objects.filter(id__in=line_ids).select_related('quote')
        pdata = proj.data or {}
        items = pdata.get('items') or []
        updates_count = 0
        for ql in quote_lines:
            q_dn = _normalize_name(ql.drawing_number or '').lower()
            q_mpn = _normalize_name(ql.mpn or '').lower()
            target_item = None
            for it in items:
                it_dn = _normalize_name(it.get('drawing_no') or it.get('item_drawing_no') or '').lower()
                it_mpn = _normalize_name(it.get('mpn') or '').lower()
                if (q_dn and it_dn and q_dn == it_dn) or (q_mpn and it_mpn and q_mpn == it_mpn):
                    target_item = it
                    break
            if target_item:
                sups = target_item.get('suppliers') or []
                if not isinstance(sups, list):
                    sups = []
                sup_entry = {
                    'supplier_name': ql.quote.supplier_name,
                    'name': ql.quote.supplier_name,
                    'price': float(ql.price_1) if ql.price_1 else None,
                    'price_1': float(ql.price_1) if ql.price_1 else None,
                    'currency': ql.quote.currency,
                    'moq': ql.moq,
                    'lead_time': ql.supplier_lead_time or ql.manufacturing_lead_time,
                    'quote_number': ql.quote.quote_number,
                    'valid_until': ql.quote.expire_date.isoformat() if ql.quote.expire_date else None,
                    'source': 'quote_export',
                    'quote_id': ql.quote.id,
                    'line_id': ql.line_number,
                    'updated_at': timezone.now().isoformat(),
                    'status': 'Quoted',
                }
                for i in range(2, 11):
                    val = getattr(ql, f'price_{i}', None)
                    if val:
                        sup_entry[f'price_{i}'] = float(val)
                found = False
                for s in sups:
                    sn = _normalize_name(s.get('supplier_name') or s.get('name') or s.get('supplier') or '').lower()
                    if sn == _normalize_name(ql.quote.supplier_name).lower():
                        s.update(sup_entry)
                        found = True
                        break
                if not found:
                    sups.append(sup_entry)
                target_item['suppliers'] = sups
                updates_count += 1
        if updates_count > 0:
            proj.data = pdata
            proj.save()
        return JsonResponse({'ok': True, 'updated': updates_count})
    except Exception:
        logger.exception('Unexpected error')
        return JsonResponse({'error': 'Server Error'}, status=500)
def quotes_bulk_import(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    payload = _json_body(request)
    if not isinstance(payload, list):
        return JsonResponse({'error': 'Expected a list of quotes'}, status=400)
    imported_count, lines_count, errors = 0, 0, []
    with transaction.atomic():
        for idx, q_data in enumerate(payload):
            sname = str(q_data.get('supplier_name') or '').strip()
            if not sname:
                errors.append(f'Row {idx+1}: Missing supplier name')
                continue
            q_num = str(q_data.get('quote_number') or '').strip()
            if not q_num:
                from django.utils import timezone
                import random
                now_str = timezone.now().strftime('%Y%m%d_%H%M')
                q_num = f"{sname.replace(' ', '_').upper()}_{now_str}_{random.randint(1000, 9999)}"
            proj = None
            q_pid = q_data.get('project_id')
            if q_pid:
                proj = _projects_qs_for_actor(actor).filter(id=str(q_pid)).first()
                if not proj:
                    errors.append(f'Row {idx+1}: invalid project_id or access denied')
                    continue
            quote, _created = _quotes_qs_for_actor(actor).update_or_create(
                quote_number=q_num,
                defaults={
                    'supplier_name': sname,
                    'source': 'import',
                    'currency': q_data.get('currency') or 'EUR',
                    'created_by': _get_buyer_username(request),
                    'project': proj,
                    'project_name': (proj.name if proj else str(q_data.get('project_name') or '')),
                    'company': (proj.company if proj else actor.get('company')),
                },
            )
            items = q_data.get('items') or []
            if not isinstance(items, list):
                continue
            start_line_num = quote.lines.count() + 1
            for i_idx, item in enumerate(items):
                QuoteLine.objects.create(
                    quote=quote,
                    line_number=start_line_num + i_idx,
                    drawing_number=str(item.get('drawing_number') or '').strip(),
                    mpn=str(item.get('mpn') or '').strip(),
                    description=str(item.get('description') or '').strip(),
                    uom=str(item.get('uom') or 'pcs'),
                    moq=_safe_int(item.get('moq'), 1),
                    manufacturing_lead_time=str(item.get('lead_time') or ''),
                    price_1=_safe_decimal(item.get('price')),
                    qty_1=str(item.get('qty') or ''),
                )
                lines_count += 1
            imported_count += 1
    return JsonResponse({'ok': True, 'imported_quotes': imported_count, 'imported_lines': lines_count, 'errors': errors})
def quotes_upsert_from_planner(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    from datetime import timedelta
    from django.utils import timezone
    try:
        data = json.loads(request.body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    pid = data.get('project_id')
    sname = (data.get('supplier_name') or '').strip()
    if not pid or not sname:
        return JsonResponse({'error': 'Missing project_id or supplier_name'}, status=400)
    try:
        proj = _projects_qs_for_actor(actor).get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)
    items_data = data.get('items') or []
    if not items_data:
        return JsonResponse({'error': 'No items provided'}, status=400)
    q_num = (data.get('quote_number') or '').strip()
    currency = data.get('currency') or 'EUR'
    expire_date_str = data.get('expire_date') or ''
    incoterms = data.get('incoterms') or ''
    shipping_cost = data.get('shipping_cost') or ''
    mov = data.get('mov') or ''
    now = timezone.now()
    user = _get_buyer_username(request)
    expire_date = None
    if expire_date_str:
        try:
            from datetime import date as dt_date
            expire_date = dt_date.fromisoformat(expire_date_str)
        except (ValueError, TypeError):
            pass
    if not expire_date:
        expire_date = (now + timedelta(days=90)).date()
    try:
        with transaction.atomic():
            is_update = False
            quote = _quotes_qs_for_actor(actor).filter(quote_number=q_num, project=proj).first() if q_num else None
            if quote:
                is_update = True
                quote.supplier_name = sname
                quote.currency = currency
                quote.expire_date = expire_date
                if incoterms:
                    quote.incoterm = incoterms
                if shipping_cost:
                    quote.shipping_cost = _safe_decimal(shipping_cost)
                if mov:
                    quote.mov = _safe_decimal(mov)
                quote.save()
                quote.lines.all().delete()
            else:
                if not q_num:
                    q_num = f"{_normalize_name(sname)[:15]}_{now.strftime('%Y%m%d_%H%M')}"
                    if _quotes_qs_for_actor(actor).filter(quote_number=q_num).exists():
                        import random
                        q_num += f"_{random.randint(100, 999)}"
                quote = Quote.objects.create(
                    id=str(uuid.uuid4()),
                    project=proj,
                    project_name=proj.name,
                    supplier_name=sname,
                    quote_number=q_num,
                    source='rfq_planner',
                    created_by=user,
                    expire_date=expire_date,
                    currency=currency,
                    incoterm=incoterms,
                    shipping_cost=_safe_decimal(shipping_cost) or Decimal('0'),
                    mov=_safe_decimal(mov) or Decimal('0'),
                )
            lines_count = 0
            for idx, item in enumerate(items_data):
                line = QuoteLine(
                    quote=quote,
                    line_number=idx + 1,
                    drawing_number=str(item.get('drawing_number') or '').strip(),
                    manufacturer=str(item.get('manufacturer') or '').strip(),
                    mpn=str(item.get('mpn') or '').strip(),
                    description=str(item.get('description') or '').strip(),
                    uom=str(item.get('uom') or 'pcs'),
                    moq=_safe_int(item.get('moq'), 1),
                    supplier_lead_time=str(item.get('lead_time') or ''),
                )
                for i in range(1, 11):
                    setattr(line, f'qty_{i}', str(item.get(f'qty_{i}') or ''))
                    setattr(line, f'price_{i}', _safe_decimal(item.get(f'price_{i}')))
                line.save()
                lines_count += 1
        return JsonResponse({'ok': True, 'quote_id': quote.id, 'quote_number': quote.quote_number, 'lines_count': lines_count, 'is_update': is_update})
    except Exception:
        logger.exception('Unexpected error')
        return JsonResponse({'error': 'Server error'}, status=500)
