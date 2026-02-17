import json
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rfq.models import Company, Project, SupplierAccess, Quote, UserCompanyProfile


def _sample_project_data():
    return {
        "name": "Test RFQ",
        "items": [
            {
                "id": "itm1",
                "item_drawing_no": "DRW-001",
                "description": "Part A",
                "manufacturer": "ACME",
                "mpn": "A-001",
                "supplier": "Supplier One",
                "qty_1": "100",
            }
        ],
    }


def _sample_project_data_two_items():
    return {
        "name": "Test RFQ 2",
        "items": [
            {
                "id": "itm1",
                "item_drawing_no": "DRW-001",
                "description": "Part A",
                "manufacturer": "ACME",
                "mpn": "A-001",
                "supplier": "Supplier One",
                "qty_1": "100",
            },
            {
                "id": "itm2",
                "item_drawing_no": "DRW-002",
                "description": "Part B",
                "manufacturer": "ACME",
                "mpn": "A-002",
                "supplier": "Supplier One",
                "qty_1": "200",
            },
        ],
    }


def _sample_project_data_two_items_no_ids():
    return {
        "name": "Test RFQ 2 no ids",
        "items": [
            {
                "id": None,
                "item_drawing_no": "DRW-001",
                "description": "Part A",
                "manufacturer": "ACME",
                "mpn": "A-001",
                "supplier": "Supplier One",
                "qty_1": "100",
            },
            {
                "id": None,
                "item_drawing_no": "DRW-002",
                "description": "Part B",
                "manufacturer": "ACME",
                "mpn": "A-002",
                "supplier": "Supplier One",
                "qty_1": "200",
            },
        ],
    }


def _sample_project_data_with_existing_main():
    return {
        "name": "Test RFQ keep main",
        "items": [
            {
                "id": "itm-main",
                "item_drawing_no": "DRW-MAIN",
                "description": "Main test",
                "manufacturer": "ACME",
                "mpn": "M-001",
                "supplier": "Supplier Existing",
                "price_1": 7.7,
                "currency": "EUR",
                "suppliers": [
                    {"name": "Supplier Existing", "supplier_name": "Supplier Existing", "isMain": True, "price": 7.7, "price_1": 7.7},
                    {"name": "Supplier One", "supplier_name": "Supplier One", "isMain": False},
                ],
                "qty_1": "100",
            }
        ],
    }


@override_settings(SECURE_SSL_REDIRECT=False, DEBUG=True, ALLOWED_HOSTS=['testserver'])
class SupplierPortalFlowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='SupplierFlow Co')
        self.buyer = get_user_model().objects.create_user(username='buyer-admin', password='x')
        UserCompanyProfile.objects.create(user=self.buyer, company=self.company, role='admin', is_active=True)
        self.client.force_login(self.buyer)

        self.project = Project.objects.create(
            id='proj1',
            name='Proj 1',
            company=self.company,
            data=_sample_project_data(),
        )
        self.token = 'tok123'
        self.access = SupplierAccess.objects.create(
            id=self.token,
            project=self.project,
            supplier_name='Supplier One',
            requested_items=[{"id": "itm1", "item_drawing_no": "DRW-001", "qty_1": "100"}],
            status='sent',
            round=1,
            submission_data={},
        )

    def test_save_draft_transitions_to_viewed(self):
        payload = {
            "supplier_contact_name": "John",
            "supplier_contact_email": "john@example.com",
            "items": [{"id": "itm1", "price": "12.5", "moq": "10"}],
        }
        r = self.client.post(
            f'/api/supplier_access/{self.token}/save_draft',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 200)
        self.access.refresh_from_db()
        self.assertEqual(self.access.status, 'viewed')
        self.assertTrue(self.access.submission_data.get('is_draft'))

    def test_submit_changes_status_to_submitted(self):
        payload = {
            "supplier_contact_name": "John",
            "items": [{"id": "itm1", "price": "12.5", "moq": "10", "lead_time": "2w"}],
        }
        r = self.client.post(
            f'/api/supplier_access/{self.token}/submit',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 200)
        self.access.refresh_from_db()
        self.assertEqual(self.access.status, 'submitted')
        self.assertIsNotNone(self.access.submitted_at)

    def test_approve_propagates_price_to_project_item(self):
        # submit first
        payload = {
            "supplier_contact_name": "John",
            "currency": "EUR",
            "items": [{"id": "itm1", "price": "12.5", "moq": "10", "lead_time": "2w"}],
        }
        r1 = self.client.post(
            f'/api/supplier_access/{self.token}/submit',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(r1.status_code, 200)

        # approve
        r2 = self.client.post(
            f'/api/supplier_access/{self.token}/approve',
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertTrue(body.get('ok'))

        self.access.refresh_from_db()
        self.assertEqual(self.access.status, 'approved')

        self.project.refresh_from_db()
        items = (self.project.data or {}).get('items') or []
        self.assertEqual(len(items), 1)
        it = items[0]
        sups = it.get('suppliers') or []
        s_one = next((s for s in sups if (s.get('supplier_name') or s.get('name')) == 'Supplier One'), None)
        self.assertIsNotNone(s_one)
        self.assertEqual(float(s_one.get('price_1')), 12.5)
        self.assertEqual(it.get('status'), 'Quoted')

    def test_submit_closed_quote_returns_403(self):
        self.access.status = 'approved'
        self.access.save(update_fields=['status'])
        r = self.client.post(
            f'/api/supplier_access/{self.token}/submit',
            data=json.dumps({"items": []}),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 403)

    def test_reopen_allowed_when_supplier_requested_it(self):
        self.access.status = 'approved'
        self.access.submission_data = {'reopen_requested': True, 'reopen_reason': 'Need correction'}
        self.access.save(update_fields=['status', 'submission_data'])

        r = self.client.post(
            f'/api/supplier_access/{self.token}/reopen',
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 200)
        self.access.refresh_from_db()
        self.assertEqual(self.access.status, 're_quote_requested')
        self.assertEqual(self.access.round, 2)
        self.assertFalse((self.access.submission_data or {}).get('reopen_requested', False))

    def test_approve_two_items_accepts_price_1_and_updates_both(self):
        project = Project.objects.create(id='proj2', name='Proj 2', company=self.company, data=_sample_project_data_two_items())
        token = 'tok-two'
        SupplierAccess.objects.create(
            id=token,
            project=project,
            supplier_name='Supplier One',
            requested_items=[
                {"id": "itm1", "item_drawing_no": "DRW-001", "qty_1": "100"},
                {"id": "itm2", "item_drawing_no": "DRW-002", "qty_1": "200"},
            ],
            status='sent',
            round=1,
            submission_data={},
        )

        payload = {
            "supplier_contact_name": "John",
            "currency": "EUR",
            "quote_number": "SUP-TEST-001",
            "items": [
                {"id": "itm1", "price_1": "12.5", "moq": "10", "lead_time": "2w"},
                {"id": "itm2", "price_1": "15.0", "moq": "20", "lead_time": "3w"},
            ],
        }
        r1 = self.client.post(
            f'/api/supplier_access/{token}/submit',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post(
            f'/api/supplier_access/{token}/approve',
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(r2.status_code, 200)

        project.refresh_from_db()
        items = (project.data or {}).get('items') or []
        by_id = {it.get('id'): it for it in items}
        s1 = next((s for s in (by_id['itm1'].get('suppliers') or []) if (s.get('supplier_name') or s.get('name')) == 'Supplier One'), None)
        s2 = next((s for s in (by_id['itm2'].get('suppliers') or []) if (s.get('supplier_name') or s.get('name')) == 'Supplier One'), None)
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        self.assertEqual(float(s1.get('price_1')), 12.5)
        self.assertEqual(float(s2.get('price_1')), 15.0)
        self.assertEqual(by_id['itm1'].get('status'), 'Quoted')
        self.assertEqual(by_id['itm2'].get('status'), 'Quoted')

        q = Quote.objects.get(project=project, quote_number='SUP-TEST-001')
        lines = list(q.lines.order_by('line_number'))
        self.assertEqual(len(lines), 2)
        self.assertEqual(float(lines[0].price_1), 12.5)
        self.assertEqual(float(lines[1].price_1), 15.0)

    def test_approve_two_items_without_ids_updates_both_by_drawing(self):
        project = Project.objects.create(id='proj3', name='Proj 3', company=self.company, data=_sample_project_data_two_items_no_ids())
        token = 'tok-no-ids'
        SupplierAccess.objects.create(
            id=token,
            project=project,
            supplier_name='Supplier One',
            requested_items=[
                {"id": None, "item_drawing_no": "DRW-001", "manufacturer": "ACME", "mpn": "A-001", "qty_1": "100"},
                {"id": None, "item_drawing_no": "DRW-002", "manufacturer": "ACME", "mpn": "A-002", "qty_1": "200"},
            ],
            status='sent',
            round=1,
            submission_data={},
        )

        payload = {
            "supplier_contact_name": "John",
            "currency": "EUR",
            "items": [
                {"id": "None", "item_drawing_no": "DRW-001", "mpn": "A-001", "price": "12.5", "moq": "10", "lead_time": "2w"},
                {"id": "None", "item_drawing_no": "DRW-002", "mpn": "A-002", "price": "15.0", "moq": "20", "lead_time": "3w"},
            ],
        }
        r1 = self.client.post(
            f'/api/supplier_access/{token}/submit',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post(
            f'/api/supplier_access/{token}/approve',
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(r2.status_code, 200)

        project.refresh_from_db()
        items = (project.data or {}).get('items') or []
        by_dn = {it.get('item_drawing_no') or it.get('drawing_no'): it for it in items}
        s1 = next((s for s in (by_dn['DRW-001'].get('suppliers') or []) if (s.get('supplier_name') or s.get('name')) == 'Supplier One'), None)
        s2 = next((s for s in (by_dn['DRW-002'].get('suppliers') or []) if (s.get('supplier_name') or s.get('name')) == 'Supplier One'), None)
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        self.assertEqual(float(s1.get('price_1')), 12.5)
        self.assertEqual(float(s2.get('price_1')), 15.0)

        q = Quote.objects.filter(project=project, supplier_name='Supplier One').order_by('-create_date').first()
        self.assertIsNotNone(q)
        lines = list(q.lines.order_by('line_number'))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].drawing_number, 'DRW-001')
        self.assertEqual(lines[0].manufacturer, 'ACME')
        self.assertEqual(lines[0].qty_1, '100')
        self.assertEqual(lines[1].drawing_number, 'DRW-002')
        self.assertEqual(lines[1].manufacturer, 'ACME')
        self.assertEqual(lines[1].qty_1, '200')

    def test_approve_does_not_override_existing_main_supplier(self):
        project = Project.objects.create(id='proj4', name='Proj 4', company=self.company, data=_sample_project_data_with_existing_main())
        token = 'tok-keep-main'
        SupplierAccess.objects.create(
            id=token,
            project=project,
            supplier_name='Supplier One',
            requested_items=[{"id": "itm-main", "item_drawing_no": "DRW-MAIN", "qty_1": "100"}],
            status='sent',
            round=1,
            submission_data={},
        )

        payload = {
            "supplier_contact_name": "John",
            "currency": "EUR",
            "items": [
                {"id": "itm-main", "price": "9.9", "moq": "10", "lead_time": "2w"},
            ],
        }
        self.client.post(
            f'/api/supplier_access/{token}/submit',
            data=json.dumps(payload),
            content_type='application/json'
        )
        r2 = self.client.post(
            f'/api/supplier_access/{token}/approve',
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(r2.status_code, 200)

        project.refresh_from_db()
        item = (project.data or {}).get('items')[0]

        # Existing main decision stays untouched
        self.assertEqual(item.get('supplier'), 'Supplier Existing')
        self.assertEqual(float(item.get('price_1')), 7.7)

        # New supplier quote still gets stored in suppliers list
        sups = item.get('suppliers') or []
        s_new = next((s for s in sups if (s.get('supplier_name') or s.get('name')) == 'Supplier One'), None)
        self.assertIsNotNone(s_new)
        self.assertEqual(float(s_new.get('price_1')), 9.9)

    def test_projects_bulk_does_not_clobber_existing_supplier_quotes(self):
        company = Company.objects.create(name='ACME CZ')
        user = get_user_model().objects.create_user(username='editor-bulk', password='x')
        UserCompanyProfile.objects.create(user=user, company=company, role='editor', is_active=True)
        self.client.force_login(user)

        project = Project.objects.create(id='proj5', name='Proj 5', company=company, data={
            'items': [{
                'id': 'itm5',
                'item_drawing_no': 'DRW-500',
                'mpn': 'M-500',
                'suppliers': [
                    {'supplier_name': 'Supplier One', 'name': 'Supplier One', 'price_1': 12.3, 'status': 'Quoted'},
                    {'supplier_name': 'Supplier Two', 'name': 'Supplier Two', 'price_1': 8.8, 'status': 'Quoted'},
                ]
            }]
        })

        stale_payload = [{
            'id': 'proj5',
            'name': 'Proj 5',
            'items': [{
                'id': 'itm5',
                'item_drawing_no': 'DRW-500',
                'mpn': 'M-500',
                'suppliers': [
                    {'supplier_name': 'Supplier One', 'name': 'Supplier One', 'price_1': 0, 'status': 'Planned'},
                    {'supplier_name': 'Supplier Two', 'name': 'Supplier Two', 'price_1': 0, 'status': 'Planned'},
                ]
            }]
        }]

        r = self.client.post('/api/projects/bulk', data=json.dumps(stale_payload), content_type='application/json')
        self.assertEqual(r.status_code, 200)

        project.refresh_from_db()
        sups = (project.data.get('items') or [])[0].get('suppliers') or []
        by_name = {(s.get('supplier_name') or s.get('name')): s for s in sups}
        self.assertEqual(float(by_name['Supplier One'].get('price_1')), 12.3)
        self.assertEqual(float(by_name['Supplier Two'].get('price_1')), 8.8)
        self.assertEqual(str(by_name['Supplier One'].get('status')), 'Quoted')
