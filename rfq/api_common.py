import json
import logging
from urllib.parse import urlparse

from django.conf import settings as django_settings
from django.http import JsonResponse

from .models import AuditLog, Company, ProjectAccess, UserCompanyProfile

logger = logging.getLogger(__name__)


def require_buyer_auth(request):
    """Return a 401 JsonResponse if request is not authenticated."""
    if request.user.is_authenticated:
        return None
    return JsonResponse({'error': 'Authentication required'}, status=401)


def require_same_origin_for_unsafe(request):
    """Extra CSRF mitigation for unsafe API methods."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None

    src = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER')
    if not src:
        return None

    try:
        parsed = urlparse(src)
        host = (parsed.hostname or '').lower()
    except Exception:
        return JsonResponse({'error': 'Invalid origin/referrer'}, status=403)

    allowed_hosts = {h.lower() for h in getattr(django_settings, 'ALLOWED_HOSTS', []) if h and h != '*'}
    trusted = set()
    for o in getattr(django_settings, 'CSRF_TRUSTED_ORIGINS', []) or []:
        try:
            trusted.add((urlparse(o).hostname or '').lower())
        except Exception:
            pass

    if host not in allowed_hosts and host not in trusted:
        return JsonResponse({'error': 'Origin not allowed'}, status=403)
    return None


def get_buyer_username(request):
    if request.user.is_authenticated:
        return request.user.username
    return 'admin'


def json_body(request):
    try:
        raw = request.body.decode('utf-8') if request.body else ''
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning('[RFQ API] JSON parse error: %s (body_len=%s)', e, len(request.body) if request.body else 0)
        return None


ROLE_ORDER = {
    'viewer': 10,
    'editor': 20,
    'admin': 30,
    'superadmin': 100,
}


def get_request_actor(request):
    """Resolve authenticated actor + RFQ profile. Returns dict or None."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None

    profile = UserCompanyProfile.objects.filter(user=user, is_active=True).select_related('company').first()
    if not profile:
        return {
            'user': user,
            'profile': None,
            'company': None,
            'role': 'viewer',
            'is_superadmin': False,
            'is_management': False,
        }

    role = (profile.role or 'viewer').lower()
    actor = {
        'user': user,
        'profile': profile,
        'company': profile.company,
        'role': role,
        'is_superadmin': role == 'superadmin',
        'is_management': bool(profile.is_management),
    }

    # Optional active company scope for superadmin (selected in app UI)
    if actor['is_superadmin']:
        selected_company_id = request.session.get('rfq_active_company_id')
        if selected_company_id not in (None, '', 'all'):
            try:
                c = Company.objects.filter(id=selected_company_id, is_active=True).first()
            except Exception:
                c = None
            if c:
                actor['scope_company'] = c
                actor['scope_company_id'] = c.id
            else:
                request.session.pop('rfq_active_company_id', None)
                actor['scope_company'] = None
                actor['scope_company_id'] = None
        else:
            actor['scope_company'] = None
            actor['scope_company_id'] = None

    return actor


def require_auth_and_profile(request):
    auth_err = require_buyer_auth(request)
    if auth_err:
        return None, auth_err

    actor = get_request_actor(request)
    if actor is None:
        return None, JsonResponse({'error': 'Authentication required'}, status=401)

    if not actor.get('is_superadmin') and not actor.get('company'):
        return None, JsonResponse({'error': 'User has no company assigned'}, status=403)

    return actor, None


def require_role(actor, min_role='viewer'):
    role = (actor or {}).get('role') or 'viewer'
    return ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(min_role, 0)


def company_qs(model, actor):
    qs = model.objects.all()
    if actor and actor.get('is_superadmin'):
        scope_company = actor.get('scope_company')
        return qs.filter(company=scope_company) if scope_company else qs
    company = (actor or {}).get('company')
    if company is None:
        return qs.none()
    return qs.filter(company=company)


def can_view_project(actor, project):
    if actor is None or project is None:
        return False
    if actor.get('is_superadmin'):
        return True

    company = actor.get('company')
    if company is None or getattr(project, 'company_id', None) != getattr(company, 'id', None):
        return False

    role = (actor.get('role') or 'viewer').lower()
    if role in ('admin',):
        return True

    # NOTE: ProjectAccess is open-by-default: if no ACL rows exist, project is viewable
    # to company users with baseline role access. Restrictions apply only when ACL rows exist.
    qs = ProjectAccess.objects.filter(project_id=project.id)
    if not qs.exists():
        return True

    if actor.get('is_management'):
        return True

    user = actor.get('user')
    if not user or not getattr(user, 'id', None):
        return False

    return qs.filter(user_id=user.id, can_view=True).exists()


def can_edit_project(actor, project):
    if not can_view_project(actor, project):
        return False

    role = (actor.get('role') or 'viewer').lower()
    if role in ('superadmin', 'admin', 'editor'):
        # still respect explicit ACL if present for non-admin roles
        if role in ('superadmin', 'admin'):
            return True
        qs = ProjectAccess.objects.filter(project_id=project.id)
        if not qs.exists():
            return True
        user = actor.get('user')
        if not user or not getattr(user, 'id', None):
            return False
        return qs.filter(user_id=user.id, can_edit=True).exists()

    return False


def audit_log(request, actor, action, entity_type='', entity_id='', project=None, metadata=None):
    """Best-effort audit log writer (never raises)."""
    try:
        user = (actor or {}).get('user')
        role = (actor or {}).get('role') or ''
        company = (actor or {}).get('company')
        ip = (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()
        ua = (request.META.get('HTTP_USER_AGENT') or '')[:255]

        AuditLog.objects.create(
            company=company,
            actor=user if getattr(user, 'is_authenticated', False) else None,
            actor_role=role,
            action=str(action or '')[:64],
            entity_type=str(entity_type or '')[:64],
            entity_id=str(entity_id or '')[:128],
            project=project,
            metadata_json=metadata or {},
            ip=ip,
            user_agent=ua,
        )
    except Exception:
        pass
