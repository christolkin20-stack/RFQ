import copy
from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from .models import SupplierAccess


def app(request, bundle_id=None):
    if not request.user.is_authenticated:
        return redirect(f"/admin/login/?next={request.path}")
    return render(request, 'rfq/app.html', {
        'build_version': 'django-v122-main-visible-stability',
    })


def mega_admin_dashboard(request):
    if not request.user.is_authenticated:
        return redirect(f"/admin/login/?next={request.path}")
    if not request.user.is_superuser:
        return HttpResponseForbidden('Superadmin only')
    return render(request, 'rfq/mega_admin.html', {})


def _merge_submitted_values(items, submission):
    """Merge submitted values back into items for read-only display."""
    if not submission.get('items'):
        return
    sub_by_id = {}
    sub_by_drawing = {}
    sub_by_mpn = {}
    for si in submission['items']:
        if not isinstance(si, dict):
            continue
        if si.get('id'):
            sub_by_id[str(si['id'])] = si
        drawing = si.get('item_drawing_no') or si.get('drawing_no')
        if drawing:
            sub_by_drawing[str(drawing)] = si
        if si.get('mpn'):
            sub_by_mpn[str(si['mpn'])] = si
    for item in items:
        sub = None
        item_id = item.get('id')
        if item_id:
            sub = sub_by_id.get(str(item_id))
        if not sub:
            drawing = item.get('item_drawing_no') or item.get('drawing_no') or ''
            if drawing:
                sub = sub_by_drawing.get(str(drawing))
        if not sub:
            mpn = item.get('mpn') or ''
            if mpn:
                sub = sub_by_mpn.get(str(mpn))
        if sub:
            item['submitted_price'] = sub.get('price', '')
            # Dynamic price tiers (2..10)
            for i in range(2, 11):
                item[f'submitted_price_{i}'] = sub.get(f'price_{i}', '')
            item['submitted_moq'] = sub.get('moq', '')
            item['submitted_lead_time'] = sub.get('lead_time', '')
            item['submitted_no_bid'] = sub.get('no_bid', False)
            item['submitted_no_bid_reason'] = sub.get('no_bid_reason', '')


def portal(request, token):
    access = get_object_or_404(SupplierAccess, id=token)

    # Cancelled portal
    if access.status == 'expired':
        return render(request, 'rfq/supplier_portal.html', {
            'access': access,
            'token': token,
            'items': [],
            'updated_items': [],
            'project': access.project,
            'supplier_name': access.supplier_name,
            'submission': {},
            'is_cancelled': True,
        })

    all_items = copy.deepcopy(access.requested_items or [])
    submission = access.submission_data or {}
    _merge_submitted_values(all_items, submission)

    # Compute dynamic qty tiers across ALL items
    max_tiers = 0
    for item in all_items:
        for i in range(1, 11):
            val = item.get(f'qty_{i}')
            if val is not None and str(val).strip():
                max_tiers = max(max_tiers, i)

    # Build generic tier header labels (qty varies per item, so header is generic)
    qty_tiers = []
    for i in range(1, max_tiers + 1):
        label = f'Price (Tier {i})'
        qty_tiers.append({'index': i, 'label': label})

    # Pre-process items: add tier_values list for template rendering
    for item in all_items:
        tier_values = []
        for i in range(1, max_tiers + 1):
            qty_val = item.get(f'qty_{i}', '')
            submitted = item.get('submitted_price' if i == 1 else f'submitted_price_{i}', '')
            tier_values.append({
                'index': i,
                'qty': qty_val,
                'submitted_price': submitted,
                'input_class': 'inp-price' if i == 1 else f'inp-price{i}',
                'has_qty': bool(str(qty_val).strip()),
            })
        item['tier_values'] = tier_values

    # Separate original items from items added later (update)
    original_items = [i for i in all_items if not i.get('added_at')]
    updated_items = [i for i in all_items if i.get('added_at')]

    return render(request, 'rfq/supplier_portal.html', {
        'access': access,
        'token': token,
        'items': original_items,
        'updated_items': updated_items,
        'all_items': all_items,
        'project': access.project,
        'supplier_name': access.supplier_name,
        'submission': submission,
        'is_cancelled': False,
        'qty_tiers': qty_tiers,
        'max_tiers': max_tiers,
    })
