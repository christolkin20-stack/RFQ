import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rfq.models import Project, Quote, QuoteLine


@override_settings(DEBUG=True, DJANGO_DEBUG=True, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class QuoteRoundtripTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='buyer', password='pass12345')
        self.client.login(username='buyer', password='pass12345')

        self.project = Project.objects.create(
            id='proj-rt-1',
            name='Roundtrip Project',
            data={
                'items': [
                    {
                        'id': 'it-1',
                        'drawing_no': 'DRW-001',
                        'description': 'Test item',
                        'manufacturer': 'ACME',
                        'mpn': 'MPN-001',
                        'qty_1': '10',
                        'qty_2': '50',
                        'suppliers': [],
                    }
                ]
            },
        )

    def test_upsert_and_export_to_item(self):
        upsert_payload = {
            'project_id': self.project.id,
            'supplier_name': 'Supplier A',
            'currency': 'EUR',
            'items': [
                {
                    'drawing_number': 'DRW-001',
                    'manufacturer': 'ACME',
                    'mpn': 'MPN-001',
                    'description': 'Test item',
                    'uom': 'pcs',
                    'moq': 1,
                    'lead_time': '2 weeks',
                    'qty_1': '10',
                    'price_1': '1.20',
                    'qty_2': '50',
                    'price_2': '1.10',
                }
            ],
        }
        resp = self.client.post(
            '/api/quotes/upsert_from_planner/',
            data=json.dumps(upsert_payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get('ok'))

        self.assertTrue(Quote.objects.filter(project=self.project, supplier_name='Supplier A').exists())
        line = QuoteLine.objects.filter(quote__project=self.project, quote__supplier_name='Supplier A').first()
        self.assertIsNotNone(line)

        export = self.client.post(
            '/api/quotes/export_to_item/',
            data=json.dumps({'project_id': self.project.id, 'line_ids': [line.id]}),
            content_type='application/json',
        )
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.json().get('ok'))

        self.project.refresh_from_db()
        item = self.project.data['items'][0]
        self.assertTrue(item.get('suppliers'))
        self.assertEqual(item['suppliers'][0].get('supplier_name'), 'Supplier A')
        self.assertEqual(item['suppliers'][0].get('price_1'), 1.2)
