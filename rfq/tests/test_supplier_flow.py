import json
from django.test import TestCase, override_settings
from rfq.models import Project, SupplierAccess


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


@override_settings(SECURE_SSL_REDIRECT=False, DEBUG=True, ALLOWED_HOSTS=['testserver'])
class SupplierPortalFlowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            id='proj1',
            name='Proj 1',
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
        self.assertEqual(it.get('supplier'), 'Supplier One')
        self.assertEqual(float(it.get('price_1')), 12.5)
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
