import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse, HttpResponseNotAllowed
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .api_common import (
    audit_log,
    can_edit_project,
    can_view_project,
    require_auth_and_profile,
    require_role,
    require_same_origin_for_unsafe,
    json_body,
)
from .models import Company, Project, Attachment, EditLock, ProjectAccess, UserCompanyProfile
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
        scope_company = actor.get('scope_company')
        return qs.filter(company=scope_company) if scope_company else qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)


def _attach_company_from_project(instance, project):
    if hasattr(instance, 'company_id') and getattr(instance, 'company_id', None) is None and project is not None:
        instance.company_id = getattr(project, 'company_id', None)


def health(request):
    return JsonResponse({'ok': True})


def session_me(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err
    user = actor.get('user')
    company = actor.get('company')
    scope_company = actor.get('scope_company')
    data = {
        'ok': True,
        'user_id': getattr(user, 'id', None),
        'username': getattr(user, 'username', ''),
        'role': actor.get('role'),
        'is_management': bool(actor.get('is_management')),
        'is_superadmin': bool(actor.get('is_superadmin')),
        'company_id': getattr(company, 'id', None),
        'company_name': getattr(company, 'name', ''),
        'scope_company_id': getattr(scope_company, 'id', None),
        'scope_company_name': getattr(scope_company, 'name', ''),
    }
    if actor.get('is_superadmin'):
        data['companies'] = [{'id': c.id, 'name': c.name} for c in Company.objects.filter(is_active=True).order_by('name')]
    return JsonResponse(data)


@csrf_exempt
def session_switch_company(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if not actor.get('is_superadmin'):
        return JsonResponse({'error': 'Superadmin permission required'}, status=403)

    payload = json_body(request)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    cid = payload.get('company_id')
    if cid in (None, '', 'all'):
        request.session['rfq_active_company_id'] = 'all'
        request.session.modified = True
        audit_log(request, actor, action='session.company_scope', entity_type='session', entity_id='all', metadata={'scope': 'all'})
        return JsonResponse({'ok': True, 'scope': 'all'})

    c = Company.objects.filter(id=cid, is_active=True).first()
    if not c:
        return JsonResponse({'error': 'Company not found'}, status=404)

    request.session['rfq_active_company_id'] = c.id
    request.session.modified = True
    audit_log(request, actor, action='session.company_scope', entity_type='session', entity_id=str(c.id), metadata={'scope': c.name})
    return JsonResponse({'ok': True, 'scope': 'company', 'company_id': c.id, 'company_name': c.name})


@csrf_exempt
def projects_collection(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method == 'GET':
        projects = [p.as_dict() for p in _projects_qs_for_actor(actor) if can_view_project(actor, p)]
        return JsonResponse({'projects': projects})

    if request.method == 'POST':
        if not require_role(actor, 'editor'):
            return JsonResponse({'error': 'Edit permission required'}, status=403)

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
            elif obj.company_id and not can_edit_project(actor, obj):
                return JsonResponse({'error': 'Access denied'}, status=403)
            obj.name = name
            obj.data = proj
            obj.save()
        audit_log(request, actor, action='project.create_or_update', entity_type='project', entity_id=obj.id, project=obj, metadata={'source': 'projects_collection'})
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
        if not can_view_project(actor, obj):
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse({'project': obj.as_dict()})

    if request.method in ('PUT', 'PATCH'):
        if not require_role(actor, 'editor'):
            return JsonResponse({'error': 'Edit permission required'}, status=403)

        payload = json_body(request)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)
        proj = payload
        name = str(proj.get('name') or 'Untitled')[:255]
        with transaction.atomic():
            obj, _created = _projects_qs_for_actor(actor).select_for_update().get_or_create(id=pid, defaults={'name': name, 'data': proj, 'company': actor.get('company')})
            if not obj.company_id and actor and actor.get('company'):
                obj.company = actor.get('company')
            elif obj.company_id and not can_edit_project(actor, obj):
                return JsonResponse({'error': 'Access denied'}, status=403)
            obj.name = name
            obj.data = proj
            obj.save()
        audit_log(request, actor, action='project.create_or_update', entity_type='project', entity_id=obj.id, project=obj, metadata={'source': 'project_detail'})
        return JsonResponse({'project': obj.as_dict()})

    if request.method == 'DELETE':
        if not require_role(actor, 'editor'):
            return JsonResponse({'error': 'Edit permission required'}, status=403)
        obj = _projects_qs_for_actor(actor).filter(id=pid).first()
        if not obj or not can_edit_project(actor, obj):
            return JsonResponse({'error': 'Not found'}, status=404)
        oid = obj.id
        obj.delete()
        audit_log(request, actor, action='project.delete', entity_type='project', entity_id=oid)
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

    if not require_role(actor, 'editor'):
        return JsonResponse({'error': 'Edit permission required'}, status=403)

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
            elif obj.company_id and not can_edit_project(actor, obj):
                return JsonResponse({'error': 'Access denied'}, status=403)
            obj.name = name
            obj.data = _merge_preserve_supplier_quotes(obj.data or {}, proj)
            obj.save()
            upserted += 1

        existing_ids = set(p.id for p in _projects_qs_for_actor(actor) if can_edit_project(actor, p))
        ids_to_delete = existing_ids - incoming_ids
        if ids_to_delete:
            deleted = _projects_qs_for_actor(actor).filter(id__in=ids_to_delete).delete()[0]

    audit_log(request, actor, action='project.bulk_sync', entity_type='project', entity_id='bulk', metadata={'upserted': upserted, 'deleted': deleted})
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
    if not require_role(actor, 'admin'):
        return JsonResponse({'error': 'Admin permission required'}, status=403)
    deleted = _projects_qs_for_actor(actor).delete()[0]
    audit_log(request, actor, action='project.reset', entity_type='project', entity_id='reset', metadata={'deleted': deleted})
    return JsonResponse({'ok': True, 'deleted': deleted})


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
        if not can_view_project(actor, proj):
            return JsonResponse({'error': 'Not found'}, status=404)
        atts = [a.as_dict() for a in Attachment.objects.filter(project=proj).order_by('-uploaded_at')]
        return JsonResponse({'attachments': atts})

    if request.method == 'POST':
        if not can_edit_project(actor, proj):
            return JsonResponse({'error': 'Access denied'}, status=403)
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
        if not can_edit_project(actor, att.project):
            return JsonResponse({'error': 'Access denied'}, status=403)
        try:
            if att.file:
                att.file.delete(save=False)
        except Exception:
            pass
        att.delete()
        return JsonResponse({'ok': True})

    return HttpResponseNotAllowed(['DELETE'])


@csrf_exempt
def project_access(request, project_id: str):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    proj = _projects_qs_for_actor(actor).filter(id=str(project_id)).first()
    if not proj:
        return JsonResponse({'error': 'Not found'}, status=404)

    if request.method == 'GET':
        if not can_view_project(actor, proj):
            return JsonResponse({'error': 'Not found'}, status=404)
        entries = ProjectAccess.objects.filter(project=proj).select_related('user').order_by('user_id')
        data = [
            {
                'user_id': e.user_id,
                'username': getattr(e.user, 'username', ''),
                'can_view': bool(e.can_view),
                'can_edit': bool(e.can_edit),
            }
            for e in entries
        ]
        return JsonResponse({'project_id': proj.id, 'access': data})

    if request.method == 'POST':
        if not require_role(actor, 'admin'):
            return JsonResponse({'error': 'Admin permission required'}, status=403)
        if not can_edit_project(actor, proj):
            return JsonResponse({'error': 'Access denied'}, status=403)

        payload = json_body(request)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)

        rows = payload.get('access')
        if not isinstance(rows, list):
            return JsonResponse({'error': 'access[] required'}, status=400)

        User = get_user_model()
        # restrict to same company unless superadmin
        allowed_users = User.objects.all()
        if not actor.get('is_superadmin'):
            company = actor.get('company')
            allowed_users = allowed_users.filter(rfq_profile__company=company, rfq_profile__is_active=True)
        allowed_ids = set(allowed_users.values_list('id', flat=True))

        with transaction.atomic():
            ProjectAccess.objects.filter(project=proj).delete()
            created = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                uid = row.get('user_id')
                try:
                    uid = int(uid)
                except Exception:
                    continue
                if uid not in allowed_ids:
                    continue
                can_view = bool(row.get('can_view', True))
                can_edit = bool(row.get('can_edit', False)) and can_view
                ProjectAccess.objects.create(
                    project=proj,
                    user_id=uid,
                    can_view=can_view,
                    can_edit=can_edit,
                    granted_by=actor.get('user') if actor and actor.get('user') and getattr(actor.get('user'), 'is_authenticated', False) else None,
                )
                created += 1

        audit_log(request, actor, action='project.access.update', entity_type='project', entity_id=proj.id, project=proj, metadata={'created': created})
        return JsonResponse({'ok': True, 'created': created})

    return HttpResponseNotAllowed(['GET', 'POST'])


@csrf_exempt
def locks_acquire(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    payload = json_body(request)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    resource_key = str(payload.get('resource_key') or '').strip()
    project_id = str(payload.get('project_id') or '').strip()
    context = str(payload.get('context') or '').strip()[:64]
    ttl_sec = int(payload.get('ttl_sec') or 180)
    ttl_sec = max(30, min(ttl_sec, 600))

    if not resource_key:
        return JsonResponse({'error': 'resource_key required'}, status=400)

    project = None
    if project_id:
        project = _projects_qs_for_actor(actor).filter(id=project_id).first()
        if not project or not can_edit_project(actor, project):
            return JsonResponse({'error': 'Access denied'}, status=403)

    company = actor.get('company')
    if not actor.get('is_superadmin') and not company:
        return JsonResponse({'error': 'User has no company assigned'}, status=403)

    now = timezone.now()
    expires_at = now + timedelta(seconds=ttl_sec)
    user = actor.get('user')
    user_id = getattr(user, 'id', None)
    display = getattr(user, 'username', '') or 'user'

    with transaction.atomic():
        lock = EditLock.objects.select_for_update().filter(resource_key=resource_key).first()
        if lock and lock.expires_at > now and lock.locked_by_id != user_id:
            return JsonResponse({
                'ok': True,
                'acquired': False,
                'owner': {
                    'user_id': lock.locked_by_id,
                    'display': lock.locked_by_display,
                },
                'expires_at': lock.expires_at.isoformat(),
                'resource_key': resource_key,
            })

        if not lock:
            lock = EditLock(resource_key=resource_key)

        lock.company = company if company else lock.company
        lock.project = project
        lock.locked_by_id = user_id
        lock.locked_by_display = display
        lock.context = context
        lock.expires_at = expires_at
        lock.save()

    audit_log(request, actor, action='lock.acquire', entity_type='lock', entity_id=resource_key, project=project, metadata={'context': context, 'ttl_sec': ttl_sec})

    return JsonResponse({
        'ok': True,
        'acquired': True,
        'resource_key': resource_key,
        'expires_at': expires_at.isoformat(),
    })


@csrf_exempt
def locks_heartbeat(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    payload = json_body(request)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    resource_key = str(payload.get('resource_key') or '').strip()
    if not resource_key:
        return JsonResponse({'error': 'resource_key required'}, status=400)

    ttl_sec = int(payload.get('ttl_sec') or 180)
    ttl_sec = max(30, min(ttl_sec, 600))
    now = timezone.now()
    expires_at = now + timedelta(seconds=ttl_sec)
    user_id = getattr(actor.get('user'), 'id', None)

    with transaction.atomic():
        lock = EditLock.objects.select_for_update().filter(resource_key=resource_key).first()
        if not lock:
            return JsonResponse({'ok': True, 'renewed': False, 'reason': 'missing'})
        if lock.expires_at <= now:
            return JsonResponse({'ok': True, 'renewed': False, 'reason': 'expired'})
        if lock.locked_by_id != user_id:
            return JsonResponse({'ok': True, 'renewed': False, 'reason': 'owner_mismatch'})
        lock.expires_at = expires_at
        lock.save(update_fields=['expires_at', 'updated_at'])

    audit_log(request, actor, action='lock.heartbeat', entity_type='lock', entity_id=resource_key, project=lock.project, metadata={'ttl_sec': ttl_sec})

    return JsonResponse({'ok': True, 'renewed': True, 'expires_at': expires_at.isoformat()})


@csrf_exempt
def locks_release(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    payload = json_body(request)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    resource_key = str(payload.get('resource_key') or '').strip()
    if not resource_key:
        return JsonResponse({'error': 'resource_key required'}, status=400)

    user_id = getattr(actor.get('user'), 'id', None)
    q = EditLock.objects.filter(resource_key=resource_key)
    if not actor.get('is_superadmin'):
        q = q.filter(locked_by_id=user_id)

    deleted, _ = q.delete()
    if deleted:
        audit_log(request, actor, action='lock.release', entity_type='lock', entity_id=resource_key)
    return JsonResponse({'ok': True, 'released': bool(deleted)})


@csrf_exempt
def locks_status(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    resource_key = str(request.GET.get('resource_key') or '').strip()
    if not resource_key:
        return JsonResponse({'error': 'resource_key required'}, status=400)

    now = timezone.now()
    lock = EditLock.objects.filter(resource_key=resource_key).first()
    if not lock or lock.expires_at <= now:
        return JsonResponse({'ok': True, 'locked': False, 'resource_key': resource_key})

    return JsonResponse({
        'ok': True,
        'locked': True,
        'resource_key': resource_key,
        'owner': {
            'user_id': lock.locked_by_id,
            'display': lock.locked_by_display,
        },
        'expires_at': lock.expires_at.isoformat(),
        'context': lock.context or '',
        'project_id': lock.project_id,
    })


@csrf_exempt
def locks_force_unlock(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if not (actor.get('is_superadmin') or require_role(actor, 'admin')):
        return JsonResponse({'error': 'Admin permission required'}, status=403)

    payload = json_body(request)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    resource_key = str(payload.get('resource_key') or '').strip()
    if not resource_key:
        return JsonResponse({'error': 'resource_key required'}, status=400)

    q = EditLock.objects.filter(resource_key=resource_key)
    if not actor.get('is_superadmin'):
        company = actor.get('company')
        q = q.filter(company=company)

    deleted, _ = q.delete()
    if deleted:
        audit_log(request, actor, action='lock.force_unlock', entity_type='lock', entity_id=resource_key)
    return JsonResponse({'ok': True, 'forced': bool(deleted)})


@csrf_exempt
def admin_users(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if not require_role(actor, 'admin'):
        return JsonResponse({'error': 'Admin permission required'}, status=403)

    company = actor.get('company')

    if request.method == 'GET':
        q = UserCompanyProfile.objects.select_related('user', 'company').filter(is_active=True)
        if not actor.get('is_superadmin'):
            q = q.filter(company=company)

        data = []
        for p in q.order_by('company_id', 'user_id'):
            data.append({
                'user_id': p.user_id,
                'username': getattr(p.user, 'username', ''),
                'email': getattr(p.user, 'email', ''),
                'company_id': p.company_id,
                'company_name': p.company.name if p.company else '',
                'role': p.role,
                'is_management': bool(p.is_management),
                'is_active': bool(p.is_active),
                'last_login': p.user.last_login.isoformat() if getattr(p.user, 'last_login', None) else None,
            })
        return JsonResponse({'users': data})

    if request.method == 'POST':
        payload = json_body(request)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)

        # create user path
        if payload.get('create_user'):
            username = str(payload.get('username') or '').strip()
            password = str(payload.get('password') or '').strip()
            email = str(payload.get('email') or '').strip()
            role = str(payload.get('role') or 'viewer').strip().lower()
            if not username or not password:
                return JsonResponse({'error': 'username and password required'}, status=400)
            if role not in {'viewer', 'editor', 'admin', 'superadmin'}:
                return JsonResponse({'error': 'Invalid role'}, status=400)
            if role == 'superadmin' and not actor.get('is_superadmin'):
                return JsonResponse({'error': 'Only superadmin can create superadmin'}, status=403)

            User = get_user_model()
            if User.objects.filter(username=username).exists():
                return JsonResponse({'error': 'Username already exists'}, status=400)

            u = User.objects.create_user(username=username, email=email, password=password)
            target_company = actor.get('company')
            if actor.get('is_superadmin') and payload.get('company_id') not in (None, '', 'null'):
                target_company = Company.objects.filter(id=payload.get('company_id')).first() or target_company

            profile = UserCompanyProfile.objects.create(
                user=u,
                company=None if role == 'superadmin' else target_company,
                role=role,
                is_management=bool(payload.get('is_management')),
                is_active=True,
            )
            audit_log(request, actor, action='admin.user.create', entity_type='user', entity_id=str(u.id), metadata={'username': username, 'role': role, 'company_id': profile.company_id})
            return JsonResponse({'ok': True, 'user_id': u.id})

        try:
            uid = int(payload.get('user_id'))
        except Exception:
            return JsonResponse({'error': 'user_id required'}, status=400)

        profile = UserCompanyProfile.objects.filter(user_id=uid).select_related('company').first()
        if not profile:
            return JsonResponse({'error': 'Profile not found'}, status=404)

        if not actor.get('is_superadmin') and profile.company_id != getattr(company, 'id', None):
            return JsonResponse({'error': 'Access denied'}, status=403)

        role = str(payload.get('role') or profile.role).strip().lower()
        allowed_roles = {'admin', 'editor', 'viewer', 'superadmin'}
        if role not in allowed_roles:
            return JsonResponse({'error': 'Invalid role'}, status=400)

        if not actor.get('is_superadmin') and role == 'superadmin':
            return JsonResponse({'error': 'Only superadmin can assign superadmin role'}, status=403)

        profile.role = role
        if 'is_management' in payload:
            profile.is_management = bool(payload.get('is_management'))
        if 'is_active' in payload:
            profile.is_active = bool(payload.get('is_active'))

        if actor.get('is_superadmin') and 'company_id' in payload:
            cid = payload.get('company_id')
            if cid in (None, '', 'null'):
                profile.company = None
            else:
                c = Company.objects.filter(id=cid).first()
                if not c:
                    return JsonResponse({'error': 'Company not found'}, status=404)
                profile.company = c

        profile.save()

        audit_log(
            request, actor,
            action='admin.user.update',
            entity_type='user',
            entity_id=str(uid),
            metadata={'role': profile.role, 'is_management': profile.is_management, 'is_active': profile.is_active, 'company_id': profile.company_id},
        )

        return JsonResponse({'ok': True})

    return HttpResponseNotAllowed(['GET', 'POST'])


@csrf_exempt
def admin_companies(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    csrf_err = require_same_origin_for_unsafe(request)
    if csrf_err:
        return csrf_err

    if not actor.get('is_superadmin'):
        return JsonResponse({'error': 'Superadmin permission required'}, status=403)

    if request.method == 'GET':
        rows = []
        for c in Company.objects.all().order_by('name'):
            rows.append({
                'id': c.id,
                'name': c.name,
                'vat_number': c.vat_number,
                'registration_number': c.registration_number,
                'address_line1': c.address_line1,
                'address_line2': c.address_line2,
                'city': c.city,
                'postal_code': c.postal_code,
                'country': c.country,
                'is_active': bool(c.is_active),
            })
        return JsonResponse({'companies': rows})

    if request.method == 'POST':
        payload = json_body(request)
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)

        cid = payload.get('id')
        if cid:
            c = Company.objects.filter(id=cid).first()
            if not c:
                return JsonResponse({'error': 'Company not found'}, status=404)
            created = False
        else:
            name = str(payload.get('name') or '').strip()
            if not name:
                return JsonResponse({'error': 'name required'}, status=400)
            c, created = Company.objects.get_or_create(name=name, defaults={'is_active': True})

        if 'name' in payload and str(payload.get('name') or '').strip():
            c.name = str(payload.get('name')).strip()
        if 'vat_number' in payload:
            c.vat_number = str(payload.get('vat_number') or '').strip()
        if 'registration_number' in payload:
            c.registration_number = str(payload.get('registration_number') or '').strip()
        if 'address_line1' in payload:
            c.address_line1 = str(payload.get('address_line1') or '').strip()
        if 'address_line2' in payload:
            c.address_line2 = str(payload.get('address_line2') or '').strip()
        if 'city' in payload:
            c.city = str(payload.get('city') or '').strip()
        if 'postal_code' in payload:
            c.postal_code = str(payload.get('postal_code') or '').strip()
        if 'country' in payload:
            c.country = str(payload.get('country') or '').strip()
        if 'is_active' in payload:
            c.is_active = bool(payload.get('is_active'))

        c.save()

        audit_log(request, actor, action='admin.company.upsert', entity_type='company', entity_id=str(c.id), metadata={'created': created, 'name': c.name, 'is_active': c.is_active})

        return JsonResponse({'ok': True, 'company': {
            'id': c.id,
            'name': c.name,
            'vat_number': c.vat_number,
            'registration_number': c.registration_number,
            'address_line1': c.address_line1,
            'address_line2': c.address_line2,
            'city': c.city,
            'postal_code': c.postal_code,
            'country': c.country,
            'is_active': c.is_active,
        }})

    return HttpResponseNotAllowed(['GET', 'POST'])



@csrf_exempt
def admin_locks(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    if not require_role(actor, 'admin'):
        return JsonResponse({'error': 'Admin permission required'}, status=403)

    now = timezone.now()
    q = EditLock.objects.select_related('locked_by', 'company', 'project').filter(expires_at__gt=now)
    if not actor.get('is_superadmin'):
        q = q.filter(company=actor.get('company'))

    try:
        limit = min(int(request.GET.get('limit', 200)), 500)
    except Exception:
        limit = 200

    rows = []
    for l in q.order_by('expires_at')[:limit]:
        rows.append({
            'resource_key': l.resource_key,
            'context': l.context,
            'project_id': l.project_id,
            'project_name': l.project.name if l.project else '',
            'company_id': l.company_id,
            'company_name': l.company.name if l.company else '',
            'locked_by': l.locked_by_display or (getattr(l.locked_by, 'username', '') if l.locked_by_id else ''),
            'locked_by_id': l.locked_by_id,
            'expires_at': l.expires_at.isoformat() if l.expires_at else '',
        })

    return JsonResponse({'locks': rows})


@csrf_exempt
def admin_audit_logs(request):
    actor, auth_err = require_auth_and_profile(request)
    if auth_err:
        return auth_err

    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    if not require_role(actor, 'admin'):
        return JsonResponse({'error': 'Admin permission required'}, status=403)

    from .models import AuditLog

    q = AuditLog.objects.select_related('actor', 'company', 'project').all()
    if not actor.get('is_superadmin'):
        q = q.filter(company=actor.get('company'))

    action = str(request.GET.get('action') or '').strip()
    if action:
        q = q.filter(action__icontains=action)

    entity = str(request.GET.get('entity_type') or '').strip()
    if entity:
        q = q.filter(entity_type__icontains=entity)

    try:
        limit = min(int(request.GET.get('limit', 100)), 500)
    except Exception:
        limit = 100

    rows = []
    for a in q.order_by('-created_at')[:limit]:
        rows.append({
            'id': a.id,
            'time': a.created_at.isoformat() if a.created_at else '',
            'action': a.action,
            'entity_type': a.entity_type,
            'entity_id': a.entity_id,
            'actor': getattr(a.actor, 'username', '') if a.actor_id else '',
            'actor_role': a.actor_role,
            'company_id': a.company_id,
            'project_id': a.project_id,
            'metadata': a.metadata_json or {},
        })

    return JsonResponse({'logs': rows})


# bridge until export module extraction
export_data = _v.export_data
