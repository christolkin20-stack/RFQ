import json
import logging
import os
import uuid
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .api_common import (
    audit_log as _audit_log,
    can_view_project as _can_view_project,
    get_buyer_username as _get_buyer_username,
    json_body as _json_body,
    require_auth_and_profile as _require_auth_and_profile,
    require_role as _require_role,
    require_same_origin_for_unsafe as _require_same_origin_for_unsafe,
)
from .models import Project, Quote, QuoteLine, SupplierAccess, SupplierAccessRound, SupplierInteractionFile
logger = logging.getLogger(__name__)
def _require_supplier_editor(actor):
    return _require_role(actor, 'editor')
def _require_supplier_admin(actor):
    return _require_role(actor, 'admin')
def _projects_qs_for_actor(actor):
    qs = Project.objects.all()
    if actor and actor.get('is_superadmin'):
        scope_company = actor.get('scope_company')
        return qs.filter(company=scope_company) if scope_company else qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)
def _supplier_access_qs_for_actor(actor):
    qs = SupplierAccess.objects.select_related('project')
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


def _is_token_expired(access):
    if not access or not getattr(access, 'valid_until', None):
        return False
    from django.utils import timezone
    return access.valid_until <= timezone.now()


def _validate_price_decimal(raw, *, max_value=Decimal('999999999.99')):
    if raw is None or str(raw).strip() == '':
        return None
    try:
        value = Decimal(str(raw).replace(',', '.').strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if value < 0 or value > max_value:
        return None
    return value
def _normalize_item_id(v):
    s = str(v or '').strip()
    if not s:
        return ''
    if s.lower() in {'none', 'null', 'undefined', 'nan'}:
        return ''
    return s
def _item_match_key(item):
    if not isinstance(item, dict):
        return ''
    sid = _normalize_item_id(item.get('id'))
    if sid:
        return f'id:{sid}'
    dn = _normalize_name(item.get('item_drawing_no') or item.get('drawing_no') or '').lower()
    if dn:
        return f'dn:{dn}'
    mpn = _normalize_name(item.get('mpn') or '').lower()
    if mpn:
        return f'mpn:{mpn}'
    return ''
def _validate_submission_prices(data):
    if not isinstance(data, dict):
        return []
    errors = []
    items = data.get('items') or []
    if not isinstance(items, list):
        return ['items must be a list']
    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        for tier in range(1, 11):
            key = 'price' if tier == 1 else f'price_{tier}'
            raw = it.get(key)
            if raw in (None, ''):
                continue
            if _validate_price_decimal(raw) is None:
                errors.append(f'item {idx}: invalid {key}')
    return errors


def _extract_items_for_supplier(project_data, supplier_name):
    items = (project_data or {}).get('items') or []
    sname_norm = _normalize_name(supplier_name).lower()
    extracted = []
    for it in items:
        if not isinstance(it, dict):
            continue
        is_assigned = _normalize_name(it.get('supplier') or '').lower() == sname_norm
        is_in_list = False
        sups = it.get('suppliers') or []
        if isinstance(sups, list):
            for s in sups:
                if isinstance(s, dict) and _normalize_name(s.get('name') or s.get('supplier') or '').lower() == sname_norm:
                    is_in_list = True
                    break
        if is_assigned or is_in_list:
            entry = {
                'id': it.get('id'),
                'item_drawing_no': it.get('item_drawing_no') or it.get('drawing_no') or '',
                'drawing_no': it.get('drawing_no') or it.get('item_drawing_no') or '',
                'line': it.get('line') or '',
                'description': it.get('description') or '',
                'manufacturer': it.get('manufacturer') or '',
                'mpn': it.get('mpn') or '',
                'uom': it.get('uom') or 'pcs',
                'target_price': it.get('target_price') or '',
            }
            for i in range(1, 11):
                key = f'qty_{i}'
                val = it.get(key)
                if val is None and i == 1:
                    val = it.get('qty') or ''
                entry[key] = val if val is not None else ''
            extracted.append(entry)
    return extracted
def supplier_access_generate(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_editor(actor):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    payload = _json_body(request) or {}
    pid = str(payload.get('project_id') or '')
    sname = str(payload.get('supplier_name') or '').strip()
    if not pid or not sname:
        return JsonResponse({'error': 'project_id and supplier_name required'}, status=400)
    try:
        proj = _projects_qs_for_actor(actor).get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)
    extracted = _extract_items_for_supplier(proj.data, sname)
    vu = None
    vu_raw = payload.get('valid_until')
    if vu_raw:
        from django.utils.dateparse import parse_date, parse_datetime
        vu = parse_datetime(str(vu_raw))
        if not vu:
            d = parse_date(str(vu_raw))
            if d:
                from datetime import datetime, time as dtime
                from django.utils import timezone
                vu = timezone.make_aware(datetime.combine(d, dtime(23, 59, 59)))
    token = uuid.uuid4().hex
    access = SupplierAccess(
        id=token,
        project=proj,
        supplier_name=sname,
        requested_items=extracted,
        submission_data={},
        status='sent',
        round=1,
        valid_until=vu,
        contact_name=str(payload.get('contact_name') or '').strip()[:255],
        contact_email=str(payload.get('contact_email') or '').strip()[:255],
        contact_phone=str(payload.get('contact_phone') or '').strip()[:64],
        instruction_message=str(payload.get('instruction_message') or '').strip(),
    )
    access.save()
    return JsonResponse({'access': access.as_dict()})
def supplier_access_viewed(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        access = SupplierAccess.objects.get(id=token)
        if access.status in ['sent', 're_quote_requested']:
            from django.utils import timezone
            if not access.viewed_at:
                access.viewed_at = timezone.now()
            access.status = 'viewed'
            access.save()
        return JsonResponse({'ok': True})
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
@csrf_exempt
def supplier_portal_save_draft(request, token):
    try:
        access = SupplierAccess.objects.get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Invalid token'}, status=404)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if access.status not in ['sent', 'viewed', 're_quote_requested']:
        return JsonResponse({'error': 'This quote is closed or already submitted.'}, status=403)
    if _is_token_expired(access):
        access.status = 'expired'
        access.save(update_fields=['status'])
        return JsonResponse({'error': 'This quote token has expired.'}, status=403)
    data = _json_body(request) or {}
    data['supplier_contact_name'] = str(data.get('supplier_contact_name') or '').strip()[:255]
    data['supplier_contact_email'] = str(data.get('supplier_contact_email') or '').strip()[:255]
    data['supplier_contact_phone'] = str(data.get('supplier_contact_phone') or '').strip()[:64]
    price_errors = _validate_submission_prices(data)
    if price_errors:
        return JsonResponse({'error': 'Invalid submitted prices', 'details': price_errors}, status=400)
    data['is_draft'] = True
    access.submission_data = data
    if access.status == 'sent':
        access.status = 'viewed'
    access.save()
    return JsonResponse({'ok': True, 'message': 'Draft saved'})
@csrf_exempt
def supplier_portal_submit(request, token):
    try:
        access = SupplierAccess.objects.get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Invalid token'}, status=404)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if access.status not in ['sent', 'viewed', 're_quote_requested']:
        return JsonResponse({'error': 'This quote is closed or already submitted.'}, status=403)
    if _is_token_expired(access):
        access.status = 'expired'
        access.save(update_fields=['status'])
        return JsonResponse({'error': 'This quote token has expired.'}, status=403)
    data = {}
    files = []
    if request.content_type.startswith('multipart'):
        raw_data = request.POST.get('data')
        if raw_data:
            try:
                data = json.loads(raw_data)
            except (ValueError, TypeError) as e:
                logger.warning('supplier_portal_submit: JSON parse error: %s', e)
        files = request.FILES.getlist('files') or request.FILES.getlist('files[]')
    else:
        data = _json_body(request) or {}
    allowed_extensions = {'.pdf', '.xlsx', '.xls', '.csv', '.doc', '.docx', '.png', '.jpg', '.jpeg'}
    max_file_size = 10 * 1024 * 1024
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in allowed_extensions:
            return JsonResponse({'error': f'File type {ext} not allowed'}, status=400)
        if f.size > max_file_size:
            return JsonResponse({'error': f'File {f.name} exceeds 10 MB limit'}, status=400)
    data['supplier_contact_name'] = str(data.get('supplier_contact_name') or '').strip()[:255]
    data['supplier_contact_email'] = str(data.get('supplier_contact_email') or '').strip()[:255]
    data['supplier_contact_phone'] = str(data.get('supplier_contact_phone') or '').strip()[:64]
    price_errors = _validate_submission_prices(data)
    if price_errors:
        return JsonResponse({'error': 'Invalid submitted prices', 'details': price_errors}, status=400)
    from django.utils import timezone
    now = timezone.now()
    with transaction.atomic():
        access.submission_data = data
        access.status = 'submitted'
        access.submitted_at = now
        access.replied_at = now
        access.save()
        SupplierAccessRound.objects.create(
            company=access.company,
            supplier_access=access,
            round=access.round,
            requested_items=access.requested_items,
            submission_data=data,
            submitted_at=now,
            buyer_decision=None,
        )
        for f in files:
            SupplierInteractionFile.objects.create(
                company=access.company,
                supplier_access=access,
                round=access.round,
                file=f,
                original_name=f.name,
                size=f.size,
                uploaded_by='supplier',
            )
    return JsonResponse({'ok': True})
def supplier_access_approve(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_admin(actor):
        return JsonResponse({'error': 'Admin permission required'}, status=403)
    try:
        access = _supplier_access_qs_for_actor(actor).get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Invalid token'}, status=404)
    from django.utils import timezone
    now = timezone.now()
    user = _get_buyer_username(request)
    submission = access.submission_data or {}
    sub_items = submission.get('items') or []
    quote_currency = str(submission.get('currency') or 'EUR')
    quote_shipping = str(submission.get('shipping') or '')
    quote_incoterms = str(submission.get('incoterms') or '')
    quote_payment_terms = str(submission.get('payment_terms') or '')
    quote_number = str(submission.get('quote_number') or '')
    quote_valid_until = str(submission.get('quote_valid_until') or '')
    quote_packaging = str(submission.get('packaging') or '')
    sub_map = {}
    sub_no_id = []
    for si in sub_items:
        if not isinstance(si, dict):
            continue
        sid = _normalize_item_id(si.get('id'))
        if sid:
            sub_map[sid] = si
        else:
            sub_no_id.append(si)
    all_candidates = list(sub_map.values()) + sub_no_id
    updates_count = 0
    unmatched_items = []
    sup_name_norm = _normalize_name(access.supplier_name).lower()
    updated_item_keys = set()
    with transaction.atomic():
        proj = _projects_qs_for_actor(actor).select_for_update().get(id=access.project_id)
        pdata = proj.data or {}
        items = pdata.get('items') or []
        for it in items:
            it_id = _normalize_item_id(it.get('id'))
            sub_entry = sub_map.get(it_id) if it_id else None
            if not sub_entry:
                p_dn = _normalize_name(it.get('item_drawing_no') or it.get('drawing_no') or '').lower()
                p_mpn = _normalize_name(it.get('mpn') or '').lower()
                for cand in all_candidates:
                    c_dn = _normalize_name(cand.get('item_drawing_no') or cand.get('drawing_no') or '').lower()
                    c_mpn = _normalize_name(cand.get('mpn') or '').lower()
                    if p_dn and c_dn and c_dn == p_dn:
                        sub_entry = cand
                        break
                    if p_mpn and c_mpn and c_mpn == p_mpn:
                        sub_entry = cand
                        break
            if not sub_entry:
                unmatched_items.append({
                    'id': it.get('id'),
                    'item_drawing_no': it.get('item_drawing_no') or it.get('drawing_no') or '',
                    'mpn': it.get('mpn') or '',
                    'reason': 'no_matching_submission_item',
                })
                continue
            price_decimal = _validate_price_decimal(sub_entry.get('price') or sub_entry.get('price_1'))
            if price_decimal is None:
                unmatched_items.append({
                    'id': it.get('id'),
                    'item_drawing_no': it.get('item_drawing_no') or it.get('drawing_no') or '',
                    'mpn': it.get('mpn') or '',
                    'reason': 'invalid_price',
                })
                continue
            new_price = float(price_decimal)
            new_moq = sub_entry.get('moq') or ''
            new_lead = sub_entry.get('lead_time') or ''
            new_comment = sub_entry.get('notes') or sub_entry.get('comment') or ''
            sups = it.get('suppliers') or []
            if not isinstance(sups, list):
                sups = []
            sup_fields = {
                'price': new_price,
                'price_1': new_price,
                'moq': new_moq,
                'lead_time': new_lead,
                'currency': quote_currency,
                'shipping': quote_shipping,
                'incoterms': quote_incoterms,
                'payment_terms': quote_payment_terms,
                'quote_number': quote_number,
                'quote_valid_until': quote_valid_until,
                'packaging': quote_packaging,
                'status': 'Quoted',
                'quote_status': 'Quoted',
                'rfq_sent_date': access.created_at.strftime('%Y-%m-%d'),
                'quote_received_date': (access.submitted_at or now).strftime('%Y-%m-%d'),
                'source': 'supplier_interaction',
                'interaction_id': access.id,
                'round': access.round,
                'note': new_comment,
            }
            for tier_i in range(2, 11):
                tk = f'price_{tier_i}'
                raw_tier = sub_entry.get(tk)
                if str(raw_tier or '').strip():
                    tier_price = _validate_price_decimal(raw_tier)
                    if tier_price is not None:
                        sup_fields[tk] = float(tier_price)
            prices_arr = []
            for tier_idx in range(1, 11):
                qty_val = it.get(f'qty_{tier_idx}')
                if (qty_val is None or not str(qty_val).strip()) and tier_idx == 1:
                    qty_val = it.get('qty') or sub_entry.get(f'qty_{tier_idx}') or sub_entry.get('qty') or ''
                if (qty_val is None or not str(qty_val).strip()) and tier_idx > 1:
                    qty_val = sub_entry.get(f'qty_{tier_idx}') or ''
                if qty_val is not None and str(qty_val).strip():
                    price_key = f'price_{tier_idx}' if tier_idx > 1 else 'price'
                    tier_price = sup_fields.get(f'price_{tier_idx}') or sup_fields.get(price_key, '')
                    prices_arr.append({'qty': qty_val, 'price': tier_price if tier_price else '', 'index': tier_idx})
            if prices_arr:
                sup_fields['prices'] = prices_arr
            found = False
            for s in sups:
                if isinstance(s, dict):
                    sname = s.get('supplier_name') or s.get('name') or s.get('supplier') or ''
                    if _normalize_name(sname).lower() == sup_name_norm:
                        s.update(sup_fields)
                        found = True
                        break
            if not found:
                sup_fields['name'] = access.supplier_name
                sup_fields['supplier_name'] = access.supplier_name
                # Do not auto-decide main supplier during Supplier Interaction approval.
                sup_fields['isMain'] = False
                sups.append(sup_fields)
            it['suppliers'] = sups
            it['price_source'] = 'supplier_interaction'
            it['last_interaction_id'] = access.id
            it['last_approved_by'] = user
            it['last_approved_at'] = now.isoformat()
            it['quote_round'] = access.round
            # IMPORTANT: do not auto-overwrite top-level winner fields on item
            # (supplier/price_1/main tier values). Winner selection belongs to
            # Price Comparison step, not Supplier Interaction approval.
            if str(it.get('status') or '') not in ('Done', 'Closed'):
                it['status'] = 'Quoted'
            updates_count += 1
            k = _item_match_key(it)
            if k:
                updated_item_keys.add(k)
        proj.data = pdata
        proj.save()
        access.status = 'approved'
        access.approved_at = now
        access.approved_by = user
        access.save()
        round_rec = SupplierAccessRound.objects.filter(supplier_access=access, round=access.round).first()
        if not round_rec:
            round_rec = SupplierAccessRound.objects.create(
                company=access.company,
                supplier_access=access,
                round=access.round,
                requested_items=access.requested_items,
                submission_data=access.submission_data,
                submitted_at=access.submitted_at,
            )
        round_rec.buyer_decision = 'approved'
        round_rec.decision_by = user
        round_rec.decision_at = now
        round_rec.save()
        from datetime import timedelta
        try:
            q_num = quote_number or f"{_normalize_name(access.supplier_name)[:15]}_{now.strftime('%Y%m%d_%H%M')}"
            existing_quote = _quotes_qs_for_actor(actor).filter(quote_number=q_num, project=proj).first()
            if existing_quote:
                existing_quote.supplier_name = access.supplier_name
                existing_quote.currency = quote_currency
                existing_quote.incoterm = quote_incoterms
                existing_quote.payment_terms = quote_payment_terms
                existing_quote.packaging = quote_packaging
                if quote_shipping:
                    existing_quote.shipping_cost = _safe_decimal(quote_shipping) or Decimal('0')
                if quote_valid_until:
                    try:
                        from datetime import date as dt_date
                        existing_quote.expire_date = dt_date.fromisoformat(quote_valid_until)
                    except (ValueError, TypeError):
                        pass
                existing_quote.save()
                existing_quote.lines.all().delete()
                quote_obj = existing_quote
            else:
                expire_date = (now + timedelta(days=90)).date()
                if quote_valid_until:
                    try:
                        from datetime import date as dt_date
                        expire_date = dt_date.fromisoformat(quote_valid_until)
                    except (ValueError, TypeError):
                        pass
                if _quotes_qs_for_actor(actor).filter(quote_number=q_num).exists():
                    import random
                    q_num += f"_{random.randint(100, 999)}"
                quote_obj = Quote.objects.create(
                    id=uuid.uuid4().hex,
                    company=proj.company,
                    project=proj,
                    project_name=proj.name,
                    supplier_name=access.supplier_name,
                    quote_number=q_num,
                    source='supplier_portal',
                    source_id=access.id,
                    created_by=user,
                    expire_date=expire_date,
                    currency=quote_currency,
                    incoterm=quote_incoterms,
                    payment_terms=quote_payment_terms,
                    packaging=quote_packaging,
                    shipping_cost=_safe_decimal(quote_shipping) or Decimal('0'),
                    received_from=submission.get('supplier_contact_name') or access.contact_name or '',
                    notes=str(submission.get('notes') or ''),
                )
            req_items = access.requested_items or []
            req_by_id = {}
            req_by_dn = {}
            req_by_mpn = {}
            for ri in req_items:
                if not isinstance(ri, dict):
                    continue
                rid = _normalize_item_id(ri.get('id'))
                if rid:
                    req_by_id[rid] = ri
                rdn = _normalize_name(ri.get('item_drawing_no') or ri.get('drawing_no') or '').lower()
                if rdn:
                    req_by_dn[rdn] = ri
                rmpn = _normalize_name(ri.get('mpn') or '').lower()
                if rmpn:
                    req_by_mpn[rmpn] = ri
            line_num = 0
            for si in sub_items:
                if not isinstance(si, dict) or si.get('no_bid'):
                    continue
                price_decimal = _validate_price_decimal(si.get('price') or si.get('price_1'))
                if price_decimal is None:
                    continue
                line_num += 1
                sid = _normalize_item_id(si.get('id'))
                sdn = _normalize_name(si.get('item_drawing_no') or si.get('drawing_no') or '').lower()
                smpn = _normalize_name(si.get('mpn') or '').lower()
                ri = req_by_id.get(sid)
                if not ri and sdn:
                    ri = req_by_dn.get(sdn)
                if not ri and smpn:
                    ri = req_by_mpn.get(smpn)
                ri = ri or {}
                ql = QuoteLine(
                    quote=quote_obj,
                    line_number=line_num,
                    drawing_number=ri.get('item_drawing_no') or ri.get('drawing_no') or si.get('item_drawing_no') or '',
                    manufacturer=ri.get('manufacturer') or si.get('manufacturer') or '',
                    mpn=ri.get('mpn') or si.get('mpn') or '',
                    description=ri.get('description') or si.get('description') or '',
                    uom=ri.get('uom') or si.get('uom') or 'pcs',
                    moq=_safe_int(si.get('moq'), 1),
                    supplier_lead_time=str(si.get('lead_time') or ''),
                    notes=str(si.get('notes') or si.get('comment') or ''),
                )
                ql.price_1 = price_decimal
                for i in range(1, 11):
                    qty_val = ri.get(f'qty_{i}') or (ri.get('qty') if i == 1 else '')
                    setattr(ql, f'qty_{i}', str(qty_val or ''))
                for i in range(2, 11):
                    raw_t = si.get(f'price_{i}')
                    if str(raw_t or '').strip():
                        t_decimal = _validate_price_decimal(raw_t)
                        if t_decimal is not None:
                            setattr(ql, f'price_{i}', t_decimal)
                ql.save()
            for it in items:
                if updated_item_keys:
                    k = _item_match_key(it)
                    if not k or k not in updated_item_keys:
                        continue
                sups = it.get('suppliers') or []
                for s in sups:
                    sname = s.get('supplier_name') or s.get('name') or ''
                    if _normalize_name(sname).lower() == sup_name_norm:
                        s['quote_id'] = quote_obj.id
                        s['quote_number'] = quote_obj.quote_number
                        break
            proj.data = pdata
            proj.save()
        except Exception:
            logger.exception('Unexpected error')
    logger.info('Approved %d/%d items for %s (unmatched: %d)', updates_count, len(items), access.supplier_name, len(unmatched_items))
    _audit_log(request, actor, action='supplier.approve', entity_type='supplier_access', entity_id=access.id, project=proj, metadata={'updated_items': updates_count, 'unmatched_items': len(unmatched_items), 'status': access.status})
    return JsonResponse({'ok': True, 'updated_items': updates_count, 'unmatched_items': unmatched_items, 'quote_number': quote_obj.quote_number if 'quote_obj' in dir() else None})
def supplier_access_reject(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_admin(actor):
        return JsonResponse({'error': 'Admin permission required'}, status=403)
    try:
        access = _supplier_access_qs_for_actor(actor).get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Invalid token'}, status=404)
    payload = _json_body(request) or {}
    action = payload.get('action')
    reason = payload.get('reason') or ''
    if not reason:
        return JsonResponse({'error': 'Reason is required'}, status=400)
    from django.utils import timezone
    now = timezone.now()
    user = _get_buyer_username(request)
    with transaction.atomic():
        round_rec = SupplierAccessRound.objects.filter(supplier_access=access, round=access.round).first()
        if not round_rec:
            round_rec = SupplierAccessRound.objects.create(
                company=access.company,
                supplier_access=access,
                round=access.round,
                requested_items=access.requested_items,
                submission_data=access.submission_data,
                submitted_at=access.submitted_at,
            )
        round_rec.decision_by = user
        round_rec.decision_at = now
        round_rec.decision_reason = reason
        if action == 're_quote':
            round_rec.buyer_decision = 're_quote'
            round_rec.save()
            access.round += 1
            access.status = 're_quote_requested'
            access.rejection_reason = reason
            access.submitted_at = None
            access.approved_at = None
            access.approved_by = None
            try:
                refreshed = _extract_items_for_supplier(access.project.data, access.supplier_name)
                if refreshed:
                    access.requested_items = refreshed
            except (AttributeError, TypeError, ValueError):
                logger.warning('Could not refresh requested_items for re-quote %s', access.id)
        elif action == 'lost':
            round_rec.buyer_decision = 'lost'
            round_rec.save()
            access.status = 'lost'
            access.rejection_reason = reason
        elif action == 'reject':
            round_rec.buyer_decision = 'rejected'
            round_rec.save()
            access.status = 'rejected'
            access.rejection_reason = reason
        else:
            return JsonResponse({'error': 'Invalid action'}, status=400)
        access.save()
    _audit_log(request, actor, action='supplier.decision', entity_type='supplier_access', entity_id=access.id, project=access.project, metadata={'new_status': access.status, 'round': access.round})
    return JsonResponse({'ok': True, 'new_status': access.status, 'round': access.round})
def project_supplier_access_list(request, project_id):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    pid = str(project_id)
    qs = _supplier_access_qs_for_actor(actor).filter(project_id=pid).order_by('-created_at')
    data = []
    for acc in qs:
        d = acc.as_dict()
        if acc.submission_data:
            sub = acc.submission_data
            d['submission_summary'] = {'notes': sub.get('notes'), 'items_count': len(sub.get('items') or [])}
        d['files'] = [f.as_dict() for f in acc.files.all()]
        d['round_history'] = []
        for r in acc.rounds.all():
            d['round_history'].append({
                'round': r.round,
                'submission_data': r.submission_data or {},
                'submitted_at': r.submitted_at.isoformat() if r.submitted_at else None,
                'buyer_decision': r.buyer_decision or '',
                'decision_reason': r.decision_reason or '',
                'decision_by': r.decision_by or '',
                'decision_at': r.decision_at.isoformat() if r.decision_at else None,
            })
        d['validation'] = {'is_complete': acc._check_completeness(), 'rejection_reason': acc.rejection_reason}
        data.append(d)
    return JsonResponse({'accesses': data})
def supplier_interaction_file_download(request, file_id):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    try:
        f = SupplierInteractionFile.objects.select_related('supplier_access', 'supplier_access__project').get(id=file_id)
    except SupplierInteractionFile.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    has_access = False
    if request.user.is_authenticated:
        actor, auth_err = _require_auth_and_profile(request)
        if not auth_err:
            scoped_access = _supplier_access_qs_for_actor(actor).filter(id=f.supplier_access_id).first()
            if scoped_access and _can_view_project(actor, scoped_access.project):
                has_access = True
    token = request.GET.get('token')
    if token and token == f.supplier_access_id:
        has_access = True
    if not has_access:
        return HttpResponse('Unauthorized', status=403)
    from django.http import FileResponse
    try:
        resp = FileResponse(f.file.open('rb'))
        resp['Content-Disposition'] = f'attachment; filename="{f.original_name}"'
        return resp
    except (FileNotFoundError, OSError):
        return HttpResponse('File missing', status=404)
def supplier_access_request_reopen(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        access = SupplierAccess.objects.get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    if access.is_editable:
        return JsonResponse({'error': 'Already editable'}, status=400)
    payload = _json_body(request) or {}
    reason = str(payload.get('reason') or '').strip()
    if not reason:
        return JsonResponse({'error': 'Reason is required'}, status=400)
    from django.utils import timezone
    sd = access.submission_data or {}
    sd['reopen_requested'] = True
    sd['reopen_requested_at'] = timezone.now().isoformat()
    sd['reopen_reason'] = reason
    access.submission_data = sd
    access.save(update_fields=['submission_data'])
    return JsonResponse({'ok': True, 'message': 'Re-opening request sent to buyer.'})
def supplier_access_update_items(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_editor(actor):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    try:
        access = _supplier_access_qs_for_actor(actor).select_for_update().get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    from django.utils import timezone
    with transaction.atomic():
        current_items = access.requested_items or []
        existing_ids, existing_drawings, existing_mpns = set(), set(), set()
        for ci in current_items:
            if ci.get('id'):
                existing_ids.add(str(ci['id']))
            drawing = ci.get('item_drawing_no') or ci.get('drawing_no') or ''
            if drawing:
                existing_drawings.add(drawing)
            if ci.get('mpn'):
                existing_mpns.add(str(ci['mpn']))
        fresh = _extract_items_for_supplier(access.project.data, access.supplier_name)
        new_items = []
        now_iso = timezone.now().isoformat()
        for fi in fresh:
            if fi.get('id') and str(fi['id']) in existing_ids:
                continue
            drawing = fi.get('item_drawing_no') or fi.get('drawing_no') or ''
            if drawing and drawing in existing_drawings:
                continue
            if fi.get('mpn') and str(fi['mpn']) in existing_mpns:
                continue
            fi['added_at'] = now_iso
            new_items.append(fi)
        if not new_items:
            return JsonResponse({'ok': True, 'message': 'No new items to add.', 'new_count': 0})
        access.requested_items = current_items + new_items
        if access.status in ('submitted', 'approved', 'rejected', 'lost'):
            access.status = 'sent'
            access.submitted_at = None
            access.approved_at = None
            access.approved_by = None
        access.save()
    return JsonResponse({'ok': True, 'message': f'{len(new_items)} item(s) added.', 'new_count': len(new_items), 'access': access.as_dict()})
def supplier_access_cancel(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_admin(actor):
        return JsonResponse({'error': 'Admin permission required'}, status=403)
    try:
        access = _supplier_access_qs_for_actor(actor).get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    access.status = 'expired'
    access.save(update_fields=['status'])
    _audit_log(request, actor, action='supplier.cancel', entity_type='supplier_access', entity_id=access.id, project=access.project, metadata={'status': access.status})
    return JsonResponse({'ok': True, 'access': access.as_dict()})
def supplier_access_reopen_buyer(request, token):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_admin(actor):
        return JsonResponse({'error': 'Admin permission required'}, status=403)
    try:
        access = _supplier_access_qs_for_actor(actor).get(id=token)
    except SupplierAccess.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    # Buyer can reopen either:
    # 1) closed portals (rejected/lost/expired), or
    # 2) any non-editable portal when supplier explicitly requested reopen.
    reopen_requested = bool((access.submission_data or {}).get('reopen_requested'))
    if access.status not in ('rejected', 'lost', 'expired') and not reopen_requested:
        return JsonResponse({'error': 'Only closed portals or supplier-requested portals can be reopened.'}, status=400)
    with transaction.atomic():
        access.round += 1
        access.status = 're_quote_requested'
        access.submitted_at = None
        access.approved_at = None
        access.approved_by = None
        sd = access.submission_data or {}
        sd.pop('reopen_requested', None)
        sd.pop('reopen_requested_at', None)
        sd.pop('reopen_reason', None)
        access.submission_data = sd
        try:
            refreshed = _extract_items_for_supplier(access.project.data, access.supplier_name)
            if refreshed:
                access.requested_items = refreshed
        except (AttributeError, TypeError, ValueError):
            logger.warning('Could not refresh items for reopen %s', access.id)
        access.save()
    _audit_log(request, actor, action='supplier.reopen', entity_type='supplier_access', entity_id=access.id, project=access.project, metadata={'status': access.status, 'round': access.round})
    return JsonResponse({'ok': True, 'access': access.as_dict()})
def supplier_access_bulk_generate(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor, auth_err = _require_auth_and_profile(request)
    if auth_err:
        return auth_err
    csrf_err = _require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err
    if not _require_supplier_editor(actor):
        return JsonResponse({'error': 'Edit permission required'}, status=403)
    payload = _json_body(request) or {}
    pid = str(payload.get('project_id') or '')
    supplier_names = payload.get('supplier_names') or []
    if not pid or not supplier_names:
        return JsonResponse({'error': 'project_id and supplier_names required'}, status=400)
    try:
        proj = _projects_qs_for_actor(actor).get(id=pid)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)
    vu = None
    vu_raw = payload.get('valid_until')
    if vu_raw:
        from django.utils.dateparse import parse_date, parse_datetime
        vu = parse_datetime(str(vu_raw))
        if not vu:
            d = parse_date(str(vu_raw))
            if d:
                from datetime import datetime, time as dtime
                from django.utils import timezone
                vu = timezone.make_aware(datetime.combine(d, dtime(23, 59, 59)))
    contact_name = str(payload.get('contact_name') or '').strip()[:255]
    contact_email = str(payload.get('contact_email') or '').strip()[:255]
    contact_phone = str(payload.get('contact_phone') or '').strip()[:64]
    instruction_message = str(payload.get('instruction_message') or '').strip()
    results = []
    for sname in supplier_names:
        sname = str(sname).strip()
        if not sname:
            continue
        extracted = _extract_items_for_supplier(proj.data, sname)
        token = uuid.uuid4().hex
        access = SupplierAccess(
            id=token,
            project=proj,
            supplier_name=sname,
            requested_items=extracted,
            submission_data={},
            status='sent',
            round=1,
            valid_until=vu,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            instruction_message=instruction_message,
        )
        access.save()
        results.append(access.as_dict())
    return JsonResponse({'ok': True, 'accesses': results, 'count': len(results)})
