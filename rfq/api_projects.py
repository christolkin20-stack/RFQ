import uuid
from django.db import transaction
from django.http import JsonResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

from .api_common import (
    require_auth_and_profile,
    require_same_origin_for_unsafe,
    json_body,
)
from .models import Project, Attachment
from . import views_api as _v


def _norm(v):
    return ' '.join(str(v or '').split()).strip().lower()


def _num(v):
    try:
        s = str(v or '').replace(',', '.').strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _item_key(it):
    if not isinstance(it, dict):
        return ''
    iid = _norm(it.get('id'))
    if iid and iid not in {'none', 'null', 'undefined', 'nan'}:
        return f'id:{iid}'
    dn = _norm(it.get('item_drawing_no') or it.get('drawing_no'))
    if dn:
        return f'dn:{dn}'
    mpn = _norm(it.get('mpn'))
    if mpn:
        return f'mpn:{mpn}'
    return ''


def _supplier_name(s):
    return _norm((s or {}).get('supplier_name') or (s or {}).get('name') or (s or {}).get('supplier'))


def _is_meaningful_quote(s):
    if not isinstance(s, dict):
        return False
    if _num(s.get('price_1') or s.get('price')) > 0:
        return True
    if str(s.get('status') or '').strip().lower() == 'quoted':
        return True
    if str(s.get('quote_status') or '').strip().lower() == 'quoted':
        return True
    return False


def _merge_preserve_supplier_quotes(existing_data, incoming_data):
    if not isinstance(existing_data, dict) or not isinstance(incoming_data, dict):
        return incoming_data

    ex_items = existing_data.get('items') or []
    in_items = incoming_data.get('items') or []
    if not isinstance(ex_items, list) or not isinstance(in_items, list):
        return incoming_data

    ex_map = {}
    for it in ex_items:
        k = _item_key(it)
        if k:
            ex_map[k] = it

    for it in in_items:
        if not isinstance(it, dict):
            continue
        k = _item_key(it)
        if not k or k not in ex_map:
            continue

        ex_it = ex_map[k]
        ex_sups = ex_it.get('suppliers') or []
        in_sups = it.get('suppliers') or []
        if not isinstance(ex_sups, list):
            ex_sups = []
        if not isinstance(in_sups, list):
            in_sups = []

        in_by_name = {_supplier_name(s): s for s in in_sups if _supplier_name(s)}
        for ex_s in ex_sups:
            nm = _supplier_name(ex_s)
            if not nm or not _is_meaningful_quote(ex_s):
                continue
            cur = in_by_name.get(nm)
            if not cur:
                in_sups.append(dict(ex_s))
                in_by_name[nm] = in_sups[-1]
                continue
            if not _is_meaningful_quote(cur):
                merged = dict(ex_s)
                merged.update(cur)
                # keep quote-bearing fields from existing when incoming is empty/stale
                for fld in ['price', 'price_1', 'price_2', 'price_3', 'price_4', 'price_5', 'status', 'quote_status', 'quote_id', 'quote_number', 'prices']:
                    if _num(cur.get(fld)) <= 0 and fld.startswith('price'):
                        merged[fld] = ex_s.get(fld)
                    elif fld in ['status', 'quote_status']:
                        cur_v = str(cur.get(fld) or '').strip().lower()
                        ex_v = str(ex_s.get(fld) or '').strip().lower()
                        if ex_v == 'quoted' and cur_v != 'quoted':
                            merged[fld] = ex_s.get(fld)
                    elif fld in ['quote_id', 'quote_number', 'prices']:
                        if not cur.get(fld):
                            merged[fld] = ex_s.get(fld)
                idx = in_sups.index(cur)
                in_sups[idx] = merged
                in_by_name[nm] = merged

        it['suppliers'] = in_sups

    incoming_data['items'] = in_items
    return incoming_data



def _projects_qs_for_actor(actor):
    qs = Project.objects.all()
    if actor and actor.get('is_superadmin'):
        return qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)


def _attach_company_from_project(instance, project):
    if hasattr(instance, 'company_id') and getattr(instance, 'company_id', None) is None and project is not None:
        instance.company_id = getattr(project, 'company_id', None)


def health(request):
    return JsonResponse({'ok': True})


@csrf_exempt
def projects_collection(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method == 'GET':
        projects = [p.as_dict() for p in _projects_qs_for_actor(actor)]
        return JsonResponse({'projects': projects})

    if request.method == 'POST':
        payload = json_body(request)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)

        proj = payload
        pid = str(proj.get('id') or uuid.uuid4().hex)
        name = str(proj.get('name') or 'Untitled')[:255]
        with transaction.atomic():
            obj, _created = _projects_qs_for_actor(actor).select_for_update().get_or_create(id=pid, defaults={'name': name, 'data': proj, 'company': actor.get('company')})
            if not obj.company_id and actor and actor.get('company'):
                obj.company = actor.get('company')
            obj.name = name
            obj.data = proj
            obj.save()
        return JsonResponse({'project': obj.as_dict()})

    return HttpResponseNotAllowed(['GET', 'POST'])


@csrf_exempt
def project_detail(request, project_id: str):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    pid = str(project_id)

    if request.method == 'GET':
        try:
            obj = _projects_qs_for_actor(actor).get(id=pid)
        except Project.DoesNotExist:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse({'project': obj.as_dict()})

    if request.method in ('PUT', 'PATCH'):
        payload = json_body(request)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)
        proj = payload
        name = str(proj.get('name') or 'Untitled')[:255]
        with transaction.atomic():
            obj, _created = _projects_qs_for_actor(actor).select_for_update().get_or_create(id=pid, defaults={'name': name, 'data': proj, 'company': actor.get('company')})
            if not obj.company_id and actor and actor.get('company'):
                obj.company = actor.get('company')
            obj.name = name
            obj.data = proj
            obj.save()
        return JsonResponse({'project': obj.as_dict()})

    if request.method == 'DELETE':
        _projects_qs_for_actor(actor).filter(id=pid).delete()
        return JsonResponse({'ok': True})

    return HttpResponseNotAllowed(['GET', 'PUT', 'PATCH', 'DELETE'])


@csrf_exempt
def projects_bulk(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    payload = json_body(request)
    projects = None
    if isinstance(payload, dict) and isinstance(payload.get('projects'), list):
        projects = payload.get('projects')
    elif isinstance(payload, list):
        projects = payload

    if not isinstance(projects, list):
        body_len = len(request.body) if request.body else 0
        return JsonResponse({'error': f'Invalid payload (expected {{projects:[...]}}), got {type(payload).__name__}, body_len={body_len}'}, status=400)

    upserted = 0
    deleted = 0
    incoming_ids = set()

    with transaction.atomic():
        for proj in projects:
            if not isinstance(proj, dict):
                continue
            pid = str(proj.get('id') or uuid.uuid4().hex)
            incoming_ids.add(pid)
            name = str(proj.get('name') or 'Untitled')[:255]
            obj, _created = _projects_qs_for_actor(actor).select_for_update().get_or_create(id=pid, defaults={'name': name, 'data': proj, 'company': actor.get('company')})
            if not obj.company_id and actor and actor.get('company'):
                obj.company = actor.get('company')
            obj.name = name
            obj.data = _merge_preserve_supplier_quotes(obj.data or {}, proj)
            obj.save()
            upserted += 1

        existing_ids = set(_projects_qs_for_actor(actor).values_list('id', flat=True))
        ids_to_delete = existing_ids - incoming_ids
        if ids_to_delete:
            deleted = _projects_qs_for_actor(actor).filter(id__in=ids_to_delete).delete()[0]

    return JsonResponse({'ok': True, 'upserted': upserted, 'deleted': deleted})


@csrf_exempt
def projects_reset(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    _projects_qs_for_actor(actor).delete()
    return JsonResponse({'ok': True})


@csrf_exempt
def project_attachments(request, project_id: str):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    try:
        proj = _projects_qs_for_actor(actor).get(id=str(project_id))
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)

    if request.method == 'GET':
        atts = [a.as_dict() for a in Attachment.objects.filter(project=proj).order_by('-uploaded_at')]
        return JsonResponse({'attachments': atts})

    if request.method == 'POST':
        f = request.FILES.get('file')
        if not f:
            return JsonResponse({'error': 'Missing file'}, status=400)
        kind = str(request.POST.get('kind') or '').strip()[:32]
        att = Attachment(id=uuid.uuid4().hex, project=proj, company=proj.company, file=f, kind=kind)
        att.save()
        return JsonResponse({'attachment': att.as_dict()})

    return HttpResponseNotAllowed(['GET', 'POST'])


@csrf_exempt
def project_attachment_detail(request, project_id: str, attachment_id: str):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    try:
        att = Attachment.objects.get(id=str(attachment_id), project__in=_projects_qs_for_actor(actor), project_id=str(project_id))
    except Attachment.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    if request.method == 'DELETE':
        try:
            if att.file:
                att.file.delete(save=False)
        except Exception:
            pass
        att.delete()
        return JsonResponse({'ok': True})

    return HttpResponseNotAllowed(['DELETE'])


# bridge until export module extraction
export_data = _v.export_data
