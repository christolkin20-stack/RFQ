import json

from django.test import TestCase, override_settings

from django.contrib.auth import get_user_model
from rfq.models import Company, Project, SupplierAccess, UserCompanyProfile


def _project_with_10_items():
    items = []
    for i in range(1, 11):
        supplier = 'Supplier One' if i <= 5 else 'Supplier Two'
        items.append(
            {
                'id': f'itm{i}',
                'item_drawing_no': f'DRW-{i:03d}',
                'description': f'Part {i}',
                'manufacturer': 'ACME',
                'mpn': f'MPN-{i:03d}',
                'supplier': supplier,
                'qty_1': '10',
            }
        )
    return {'name': 'RC1 Dryrun', 'items': items}


@override_settings(SECURE_SSL_REDIRECT=False, DEBUG=True, ALLOWED_HOSTS=['testserver'])
class RC1DryrunTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='RC1 Co')
        self.user = get_user_model().objects.create_user(username='rc1-admin', password='x')
        UserCompanyProfile.objects.create(user=self.user, company=self.company, role='admin', is_active=True)
        self.client.force_login(self.user)

        self.project = Project.objects.create(id='rc1-proj', name='RC1 Project', company=self.company, data=_project_with_10_items())
        self.acc1 = SupplierAccess.objects.create(
            id='tok-s1',
            project=self.project,
            supplier_name='Supplier One',
            requested_items=[{'id': f'itm{i}', 'item_drawing_no': f'DRW-{i:03d}', 'qty_1': '10'} for i in range(1, 6)],
            status='sent',
            round=1,
            submission_data={},
        )
        self.acc2 = SupplierAccess.objects.create(
            id='tok-s2',
            project=self.project,
            supplier_name='Supplier Two',
            requested_items=[{'id': f'itm{i}', 'item_drawing_no': f'DRW-{i:03d}', 'qty_1': '10'} for i in range(6, 11)],
            status='sent',
            round=1,
            submission_data={},
        )

    def _submit_and_approve(self, token, start_idx, end_idx, price_base):
        items = []
        for i in range(start_idx, end_idx + 1):
            items.append(
                {
                    'id': f'itm{i}',
                    'item_drawing_no': f'DRW-{i:03d}',
                    'mpn': f'MPN-{i:03d}',
                    'price': f'{price_base + i / 100:.2f}',
                    'moq': '1',
                    'lead_time': '2w',
                }
            )

        submit_payload = {'supplier_contact_name': 'QA', 'currency': 'EUR', 'items': items}
        r_submit = self.client.post(
            f'/api/supplier_access/{token}/submit',
            data=json.dumps(submit_payload),
            content_type='application/json',
        )
        self.assertEqual(r_submit.status_code, 200)

        r_approve = self.client.post(
            f'/api/supplier_access/{token}/approve',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(r_approve.status_code, 200)
        self.assertTrue(r_approve.json().get('ok'))

    def test_supplier_roundtrip_two_suppliers_ten_items(self):
        self._submit_and_approve('tok-s1', 1, 5, 1.00)
        self._submit_and_approve('tok-s2', 6, 10, 1.20)

        self.project.refresh_from_db()
        items = (self.project.data or {}).get('items') or []
        self.assertEqual(len(items), 10)

        quoted = [it for it in items if it.get('status') == 'Quoted']
        self.assertEqual(len(quoted), 10)

        for it in items:
            self.assertTrue(it.get('suppliers'))
            first_sup = (it.get('suppliers') or [None])[0] or {}
            self.assertTrue(first_sup.get('price_1') is not None or first_sup.get('price') is not None)
