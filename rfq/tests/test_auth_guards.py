from django.test import TestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=False, DEBUG=False, ALLOWED_HOSTS=['testserver'])
class AuthGuardTests(TestCase):
    def test_export_requires_auth(self):
        r = self.client.post('/api/export', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_projects_bulk_requires_auth(self):
        r = self.client.post('/api/projects/bulk', data='{"projects": []}', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_quotes_list_requires_auth(self):
        r = self.client.get('/api/quotes/')
        self.assertEqual(r.status_code, 401)

    def test_quotes_create_requires_auth(self):
        r = self.client.post('/api/quotes/create/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_supplier_access_generate_requires_auth(self):
        r = self.client.post('/api/supplier_access/generate', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)


@override_settings(SECURE_SSL_REDIRECT=False, DEBUG=True, ALLOWED_HOSTS=['testserver'])
class DebugModeBehaviorTests(TestCase):
    def test_projects_bulk_invalid_payload_in_debug_still_requires_auth(self):
        r = self.client.post('/api/projects/bulk', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_quotes_create_invalid_payload_in_debug_still_requires_auth(self):
        r = self.client.post('/api/quotes/create/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)
